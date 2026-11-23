import logging
import json
from datetime import datetime, timedelta
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from app.classes.models.users import Users
from app.classes.helpers.helpers import Helpers
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)
auth_log = logging.getLogger("auth")
login_schema = {
    "type": "object",
    "properties": {
        "username": {
            "type": "string",
            "maxLength": 20,
            "minLength": 3,
            "pattern": "^[a-zA-Z0-9_-]+$",
        },
        "password": {
            "type": "string",
            "minLength": 8,
        },
        "totp": {
            "type": "string",
            "pattern": r"^(\d{6})$",
            "error": "2FAerror",
        },
        "backup_code": {
            "type": "string",
            "pattern": r"^(.{19})$",
            "error": "2FAerror",
        },
    },
    "required": ["username", "password"],
    "additionalProperties": False,
}


class ApiAuthLoginHandler(BaseApiHandler):
    def is_cooldown(
        self,
    ):  # ToDo create type hint on return when we force py3.10 or higher
        # Check for active cooldown
        current_time = datetime.now()  # Get current time
        cooldown_until = self.controller.auth_tracker.get(
            self.request.remote_ip, {}
        ).get(
            "cooldown_until", None
        )  # Check auth_tracker for active cooldown
        if cooldown_until and cooldown_until > current_time:  # Check if there is a
            # cooldown and if it is currently active
            cooldown_remaining = (cooldown_until - current_time).seconds
            minutes, seconds = divmod(cooldown_remaining, 60)
            return f"{int(minutes):02}:{int(seconds):02}"  # Calc and return
        # remaining time for login message
        return False  # If there is no cooldown we just return false

    def is_max_failures(self) -> bool:
        if len(self.get_recent_attempts()) >= self.helper.get_setting(
            "max_login_attempts", 3
        ):  # Check if recent attempts is more than
            # user defined max
            if not self.is_cooldown():  # If we're not on cooldown we're going to
                # activate it
                self.controller.auth_tracker[self.request.remote_ip][
                    "cooldown_until"
                ] = datetime.now() + timedelta(0, 300)
            return True
        return False

    def get_recent_attempts(self) -> list:
        timestamps = (
            self.controller.auth_tracker.get(self.request.remote_ip, {})
            .get("login", {})
            .get("times", [])
        )  # Get timestamps for this IP
        # Parse the timestamps and check if they're within the last 3 minutes
        now = datetime.now()
        three_minutes_ago = now - timedelta(minutes=3)

        # Filter the timestamps
        recent_timestamps = [
            ts
            for ts in timestamps
            if datetime.strptime(ts, "%d/%m/%Y %H:%M:%S") >= three_minutes_ago
        ]
        return recent_timestamps

    def post(self):
        try:
            data = json.loads(self.request.body)  # Get request payload
        except json.decoder.JSONDecodeError as e:
            logger.error(
                "Invalid JSON schema for API"
                f" login attempt from {self.get_remote_ip()}"
            )
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, login_schema)
        except ValidationError as why:
            logger.error(
                "Invalid JSON schema for API"
                f" login attempt from {self.get_remote_ip()}"
            )
            offending_key = ""
            if why.schema.get("fill", None):
                offending_key = why.path[0] if why.path else None
            err = f"""{offending_key} {self.translator.translate(
                "validators",
                why.schema.get("error", "additionalProperties"),
                self.helper.get_setting("language"),
            )} {why.schema.get("enum", "")}"""
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INCORRECT_CREDENTIALS",
                    "error_data": self.helper.translation.translate(
                        "login", "incorrect", self.helper.get_setting("language")
                    ),
                },
            )  # Generic incorrerct message even on invalid json schema. Trying to
        # prevent an attacker from having easy access to password/user requirements
        # Sure, they could just check our open source code ¬_¬

        self.is_max_failures()  # check if user has reached max failures in 3 minutes
        cooldown = self.is_cooldown()  # Check if we have a cooldown active
        global_lang = self.helper.get_setting("language")
        if cooldown:
            err = self.helper.translation.translate("login", "cooldown", global_lang)
            return self.finish_json(
                429,  # HTTP 429 Too Many Requests
                {
                    "status": "error",
                    "error": "TOO_MANY_ATTEMPTS",
                    "error_data": err,
                    "cooldown_time": cooldown,
                },
            )

        username = str(data["username"]).lower()  # We're going to lower the username
        # because they can only be lowercase
        password = data["password"]
        totp = data.get("totp")  # We may not have totp or backupcode everytime
        backup_code = data.get("backup_code")
        # pylint: disable=no-member
        user_data = Users.get_or_none(Users.username == username)

        if user_data is None:
            self.controller.log_attempt(self.get_remote_ip(), username)
            auth_log.error(
                f"User attempted to log into {username}."
                " Authentication failed from remote IP"
                f" {self.get_remote_ip()}. User not found"
            )
            return self.finish_json(
                401,
                {
                    "status": "error",
                    "error": "INCORRECT_CREDENTIALS",
                    "error_data": self.helper.translation.translate(
                        "login", "incorrect", self.helper.get_setting("language")
                    ),
                },
            )

        if not user_data.enabled:
            auth_log.error(
                f"User attempted to log into {username}."
                " Authentication failed from remote"
                f" IP {self.get_remote_ip()} account disabled"
            )
            self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "ACCOUNT_DISABLED",
                    "error_data": "ACCOUNT DISABLED",
                    "token": None,
                },
            )
            return

        # Only block password auth if passkeys are enabled as an alternative.
        # If passkeys are globally disabled, allow password login to prevent lockout.
        if user_data.disable_password_auth and self.controller.passkey.is_enabled():
            auth_log.error(
                f"Password login attempted for {username} but password auth is"
                " disabled."
                f" Remote IP: {self.get_remote_ip()}"
            )
            self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "PASSWORD_AUTH_DISABLED",
                    "error_data": self.helper.translation.translate(
                        "passkey",
                        "passwordAuthDisabled",
                        self.helper.get_setting("language"),
                    ),
                    "token": None,
                },
            )
            return

        # Establish login result
        login_result = None
        # Verify user password
        pass_login_result = self.helper.verify_pass(password, user_data.password)

        # Get the length of user TOTP actions
        totp_enabled = len(list(user_data.totp_user)) > 0

        totp_login_result = False
        # Check if user has TOTP and if we got any type of TOTP data in the login
        # payload
        valid_backup_code = False
        if totp_enabled and not totp and backup_code:
            # Check for backup code
            lowered_backup_code = str(backup_code).replace("-", "").lower()
            for code in user_data.recovery_user:
                totp_login_result = self.helper.verify_pass(
                    lowered_backup_code, code.recovery_secret
                )

                # If we match a valid backup code we'll break out of the loop
                if totp_login_result:
                    valid_backup_code = code
                    break
            try:
                if valid_backup_code:
                    self.controller.totp.remove_recovery_code(
                        user_data.user_id, valid_backup_code
                    )
            except RuntimeError:
                self.finish_json(
                    401,
                    {
                        "status": "error",
                        "error": "INCORRECT_CREDENTIALS",
                        "error_data": self.helper.translation.translate(
                            "login", "incorrect", self.helper.get_setting("language")
                        ),
                    },
                )
            login_result = pass_login_result is True and totp_login_result is True
        elif totp_enabled and totp:
            totp_login_result = self.controller.totp.validate_user_totp(
                user_data.user_id, totp
            )
            # Check if both password auth and totp auth passed
            login_result = pass_login_result is True and totp_login_result is True
        elif (not totp_enabled and not totp) and (not totp_enabled and not backup_code):
            # If the user doesn't have TOTP enabled and they didn't send a TOTP code
            # We'll pass them through
            login_result = pass_login_result

        # Valid Login
        if login_result:
            auth_log.info(
                f"{username} successfully"
                " authenticated and logged"
                f" into panel from remote IP {self.get_remote_ip()}"
            )
            logger.info(f"User: {user_data} Logged in from IP: {self.get_remote_ip()}")

            # record this login
            query = Users.select().where(Users.username == username.lower()).get()
            query.last_ip = self.get_remote_ip()
            query.last_login = Helpers.get_time_as_string()
            query.save()

            # log this login
            self.controller.management.add_to_audit_log(
                user_data.user_id, "logged in via the API", None, self.get_remote_ip()
            )
            token = self.controller.authentication.generate(
                user_data.user_id, {"mfa": totp_login_result}
            )

            self.set_current_user(user_data.user_id, token)
            if valid_backup_code:
                return self.finish_json(
                    200,
                    {
                        "status": "ok",
                        "data": {
                            "token": token,
                            "user_id": str(user_data.user_id),
                            "page": "/panel/dashboard",
                            "warning": self.helper.translation.translate(
                                "login",
                                "burnedBackupCode",
                                self.controller.users.get_user_lang_by_id(
                                    user_data.user_id
                                ),
                            ),
                        },
                    },
                )

            return self.finish_json(
                200,
                {
                    "status": "ok",
                    "data": {
                        "token": token,
                        "user_id": str(user_data.user_id),
                        "page": "/panel/dashboard",
                    },
                },
            )

        # log this failed login attempt
        self.controller.management.add_to_audit_log(
            user_data.user_id, "Tried to log in", None, self.get_remote_ip()
        )
        self.controller.log_attempt(self.get_remote_ip(), username)
        # Setup error message for failed login
        error_msg = self.helper.translation.translate(
            "login", "incorrect", self.helper.get_setting("language")
        )
        if password == "app/config/default-creds.txt":
            error_msg += ". "
            error_msg += self.helper.translation.translate(
                "login", "defaultPath", self.helper.get_setting("language")
            )
        return self.finish_json(
            401,
            {
                "status": "error",
                "error": "INCORRECT_CREDENTIALS",
                "error_data": error_msg,
            },
        )

    def set_current_user(self, user_id: str = None, token: str = None):
        expire_days = self.helper.get_setting("cookie_expire")

        # if helper comes back with false
        if not expire_days:
            expire_days = "5"

        if user_id is not None:
            self.set_cookie(
                "token",
                token,
                expires_days=int(expire_days),
            )
        else:
            self.clear_cookie("token")
