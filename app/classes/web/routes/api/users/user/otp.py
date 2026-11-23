import json
import logging

from jsonschema import ValidationError, validate
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

totp_verify_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 3,
            "error": "mfaName",
            "fill": True,
        },
        "totp": {"type": "integer", "minLength": 6, "maxLength": 6},
    },
    "additionalProperties": False,
    "minProperties": 1,
}


class APIUsersTOTPIndexHandler(BaseApiHandler):
    def post(self, user_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data

        if str(user_id) in ["@me", str(user["user_id"])]:
            user_id = user["user_id"]
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            user = self.controller.users.get_user_by_id(user_id)
            if not user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )
        otp = self.controller.totp.create_user_totp(user_id)
        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {"otp": otp},
            },
        )

    def get(self, user_id: str, totp_id=None):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data
        if totp_id:
            return

        if str(user_id) in ["@me", str(user["user_id"])]:
            user_id = user["user_id"]
            res_user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            res_user = self.controller.users.get_user_object(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )

        codes = []
        user_totp = list(res_user.totp_user)
        for totp in user_totp:
            codes.append({"name": totp.name, "id": totp.id})

        self.finish_json(
            200,
            {"status": "ok", "data": codes},
        )


class APIUsersTOTPVerifyIndexHandler(BaseApiHandler):
    def post(self, user_id, totp_id):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            _,
            _,
            _,
        ) = auth_data
        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        try:
            validate(data, totp_verify_schema)
        except ValidationError as why:
            offending_key = why.path[0] if why.path else None
            err = f"""{self.translator.translate(
                "validators",
                why.schema.get("error", "additionalProperties"),
                self.controller.users.get_user_lang_by_id(auth_data[4]["user_id"]),
            )} {offending_key}"""
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": f"{str(err)}",
                },
            )

        if str(user_id) in ["@me", str(auth_data[4]["user_id"])]:
            user_id = auth_data[4]["user_id"]
            res_user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            res_user = self.controller.users.get_user_object(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )
        verified = self.controller.totp.verify_user_totp(
            res_user.user_id, totp_id, data.get("name"), data.get("totp")
        )  # In this step we only iterate through the request user's TOTP so this will
        # validate the user identity itself.

        if not verified:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "otp", "verify", auth_data[4]["lang"]
                    ),
                },
            )
        recovery = self.controller.totp.create_missing_backup_codes(
            auth_data[4]["user_id"]
        )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"successfully added MFA {totp_id}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )
        return self.finish_json(
            200, {"status": "ok", "data": {"backup_codes": recovery}}
        )


class APIUsersTOTPHandler(BaseApiHandler):
    def get(self, user_id: str, totp_id=None):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data
        if totp_id:
            return

        if str(user_id) in ["@me", str(user["user_id"])]:
            user_id = user["user_id"]
            res_user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            res_user = self.controller.users.get_user_object(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )

        codes = []
        user_totp = list(res_user.totp_user)
        for totp in user_totp:
            codes.append({"name": totp.name, "id": totp.id})

        self.finish_json(
            200,
            {"status": "ok", "data": codes},
        )

    def delete(self, user_id: str, totp_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data
        if not totp_id:
            return self.finish_json(
                500,
                {
                    "status": "error",
                    "error": "INVALID_REQUEST",
                    "error_data": self.helper.translation.translate(
                        "userConfig",
                        "totpIdReq",
                        self.controller.users.get_user_lang_by_id(
                            auth_data[4]["user_id"]
                        ),
                    ),
                },
            )

        if str(user_id) in ["@me", str(user["user_id"])]:
            user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission
            user = self.controller.users.get_user_object(user_id)
        role_mfa = False
        for role in self.controller.users.get_user_roles_id(user.user_id):
            if self.controller.roles.get_role(role)["mfa_required"]:
                role_mfa = True
                break
        if (
            (len(list(user.totp_user)) <= 1 and user.superuser)
            and self.helper.get_setting("superMFA")
            or (len(list(user.totp_user)) <= 1 and role_mfa)
        ):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "otp", "otpReq", self.helper.get_setting("language")
                    ),
                },
            )
        if not self.controller.totp.delete_user_totp(totp_id, user.user_id):
            return self.finish_json(404, {"status": "error", "error": "TOTP_NOT_FOUND"})
        self.controller.management.add_to_audit_log(
            user.user_id,
            f"deleted the user MFA {totp_id}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )

        self.finish_json(
            200,
            {"status": "ok"},
        )


class APIUsersTOTPRecovery(BaseApiHandler):
    def get(self, user_id: str, totp_id=None):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data
        if totp_id:
            return

        if str(user_id) in ["@me", str(user["user_id"])]:
            user_id = user["user_id"]
            res_user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            res_user = self.controller.users.get_user_object(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )
        if len(list(res_user.totp_user)) == 0:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "otp", "backupOtp", auth_data[4]["lang"]
                    ),
                },
            )

        self.controller.totp.remove_all_recovery_codes(res_user.user_id)

        backup_codes = self.controller.totp.create_missing_backup_codes(
            res_user.user_id
        )

        self.finish_json(
            200,
            {"status": "ok", "data": {"backup_codes": backup_codes}},
        )
