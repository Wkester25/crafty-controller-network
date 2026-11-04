import json
import logging

from jsonschema import ValidationError, validate
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

passkey_register_verify_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
        },
        "credential": {"type": "object"},
    },
    "required": ["credential"],
    "additionalProperties": False,
}


class ApiUsersPasskeyIndexHandler(BaseApiHandler):
    def get(self, user_id: str):
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
            res_user = self.controller.users.get_user_object(user_id)
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {"status": "error", "error": "NOT_AUTHORIZED"},
            )
        else:
            res_user = self.controller.users.get_user_object(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {"status": "error", "error": "USER_NOT_FOUND"},
                )

        passkeys = []
        for pk in res_user.passkey_user:
            passkeys.append(
                {
                    "id": pk.id,
                    "name": pk.name,
                    "device_type": pk.device_type,
                    "backed_up": pk.backed_up,
                    "created_at": str(pk.created_at),
                    "last_used_at": str(pk.last_used_at) if pk.last_used_at else None,
                }
            )

        return self.finish_json(200, {"status": "ok", "data": passkeys})

    def post(self, user_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return

        if not self.controller.passkey.is_enabled():
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "PASSKEY_DISABLED",
                    "error_data": self.helper.translation.translate(
                        "passkey",
                        "disabled",
                        self.helper.get_setting("language"),
                    ),
                },
            )

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
            return self.finish_json(403, {"status": "error", "error": "NOT_AUTHORIZED"})
        else:
            res_user = self.controller.users.get_user_by_id(user_id)
            if not res_user:
                return self.finish_json(
                    404, {"status": "error", "error": "USER_NOT_FOUND"}
                )

        result = self.controller.passkey.generate_registration_options(int(user_id))

        return self.finish_json(
            200,
            {"status": "ok", "data": result},
        )


class ApiUsersPasskeyVerifyHandler(BaseApiHandler):
    def post(self, user_id: str, challenge_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_session_for_self(auth_data, user_id):
            return

        if not self.controller.passkey.is_enabled():
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "PASSKEY_DISABLED",
                    "error_data": self.helper.translation.translate(
                        "passkey",
                        "disabled",
                        self.helper.get_setting("language"),
                    ),
                },
            )

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
            return self.finish_json(403, {"status": "error", "error": "NOT_AUTHORIZED"})

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        try:
            validate(data, passkey_register_verify_schema)
        except ValidationError as e:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(e),
                },
            )

        passkey = self.controller.passkey.verify_registration(
            user_id=int(user_id),
            challenge_id=challenge_id,
            credential_name=data.get("name", "Passkey"),
            response=data["credential"],
        )

        if not passkey:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "VERIFICATION_FAILED",
                    "error_data": self.helper.translation.translate(
                        "passkey",
                        "verificationFailed",
                        self.helper.get_setting("language"),
                    ),
                },
            )

        self.controller.management.add_to_audit_log(
            user_id,
            f"registered passkey: {passkey.name}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )

        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "id": passkey.id,
                    "name": passkey.name,
                },
            },
        )


class ApiUsersPasskeyHandler(BaseApiHandler):
    def delete(self, user_id: str, passkey_id: str):
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
            return self.finish_json(403, {"status": "error", "error": "NOT_AUTHORIZED"})

        result = self.controller.passkey.delete_passkey(passkey_id, int(user_id))

        if not result:
            return self.finish_json(
                404, {"status": "error", "error": "PASSKEY_NOT_FOUND"}
            )

        self.controller.management.add_to_audit_log(
            user_id,
            f"deleted passkey {passkey_id}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )

        return self.finish_json(200, {"status": "ok"})
