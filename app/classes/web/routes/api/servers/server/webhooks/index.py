# TODO: create and read

import json
import logging

from jsonschema import ValidationError, validate
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.web.webhooks.webhook_factory import WebhookFactory

URL_REGEX_PATTERN = (
    r"^(https?://)?"
    r"((([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})"
    r"|(((25[0-5]|(2[0-4]|1\d|[1-9])?\d)\.){3}(25[0-5]|(2[0-4]|1\d|[1-9])?\d))"
    r"|(\[[a-fA-F0-9:]+\]))"
    r"(:(6553[0-5]|655[0-2]\d|65[0-4]\d{2}|6[0-4]\d{3}|[1-5]\d{4}|[1-9]\d{0,3}))?"
    r"(/.*)?$"
)
logger = logging.getLogger(__name__)
new_webhook_schema = {
    "type": "object",
    "properties": {
        "webhook_type": {
            "type": "string",
            "enum": WebhookFactory.get_supported_providers(),
            "error": "typeString",
            "fill": True,
        },
        "name": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "url": {
            "type": "string",
            "pattern": URL_REGEX_PATTERN,
            "error": "typeString",
            "fill": True,
        },
        "bot_name": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "trigger": {
            "type": "array",
            "error": "typeString",
            "fill": True,
        },
        "body": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "color": {
            "type": "string",
            "default": "#005cd1",
            "error": "typeString",
            "fill": True,
        },
        "enabled": {
            "type": "boolean",
            "default": True,
            "error": "typeBool",
            "fill": True,
        },
    },
    "additionalProperties": False,
    "minProperties": 7,
}


class ApiServersServerWebhooksIndexHandler(BaseApiHandler):
    def get(self, server_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.CONFIG not in server_permissions:
            # if the user doesn't have Schedule permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        self.finish_json(
            200,
            {
                "status": "ok",
                "data": self.controller.management.get_webhooks_by_server(server_id),
            },
        )

    def post(self, server_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        try:
            validate(data, new_webhook_schema)
        except ValidationError as why:
            offending_key = ""
            if why.schema.get("fill", None):
                offending_key = why.path[0] if why.path else None
            err = f"""{offending_key} {self.translator.translate(
                "validators",
                why.schema.get("error", "additionalProperties"),
                self.controller.users.get_user_lang_by_id(auth_data[4]["user_id"]),
            )} {why.schema.get("enum", "")}"""
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": f"{str(err)}",
                },
            )

        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            # if the user doesn't have access to the server, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.CONFIG not in server_permissions:
            # if the user doesn't have Schedule permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        data["server_id"] = server_id

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: added webhook",
            server_id,
            self.get_remote_ip(),
        )
        triggers = ""
        for item in data["trigger"]:
            string = item + ","
            triggers += string
        data["trigger"] = triggers
        webhook_id = self.controller.management.create_webhook(data)

        self.finish_json(200, {"status": "ok", "data": {"webhook_id": webhook_id}})
