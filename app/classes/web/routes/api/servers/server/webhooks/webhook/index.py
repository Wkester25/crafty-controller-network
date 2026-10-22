# TODO: read and delete

import json
import logging

from jsonschema import ValidationError, validate
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.webhooks.webhook_factory import WebhookFactory
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

webhook_patch_schema = {
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
    "minProperties": 1,
}


class ApiServersServerWebhooksManagementIndexHandler(BaseApiHandler):
    def _server_has_webhook(self, server_id: str, webhook_id: int) -> bool:
        """Checks to make sure the target webhook is owned by the target server.

        Args:
            server_id (str): target server ID that user has permission for.
            webhook_id (int): target schedule ID.

        Returns:
            bool: Returns true if the target schedule ID is owned by the target server.
        """
        webhooks = self.controller.management.get_webhooks_by_server(server_id, True)
        server_webhooks = [webhook.id for webhook in webhooks]
        print(server_webhooks, webhook_id)
        return int(webhook_id) in server_webhooks

    def get(self, server_id: str, webhook_id: str):
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
        if (
            EnumPermissionsServer.CONFIG not in server_permissions
            or not self._server_has_webhook(server_id, webhook_id)
        ):
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
        if (
            not str(webhook_id)
            in self.controller.management.get_webhooks_by_server(server_id).keys()
        ):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NO WEBHOOK FOUND",
                    "error_data": "NOT FOUND",
                },
            )
        self.finish_json(
            200,
            {
                "status": "ok",
                "data": self.controller.management.get_webhook_by_id(webhook_id),
            },
        )

    def delete(self, server_id: str, webhook_id: str):
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
        if (
            EnumPermissionsServer.CONFIG not in server_permissions
            or not self._server_has_webhook(server_id, webhook_id)
        ):
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

        try:
            self.controller.management.delete_webhook(webhook_id)
        except Exception:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NO WEBHOOK FOUND",
                    "error_data": "NOT FOUND",
                },
            )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: removed webhook",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(200, {"status": "ok"})

    def patch(self, server_id: str, webhook_id: str):
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
            validate(data, webhook_patch_schema)
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
        if (
            EnumPermissionsServer.CONFIG not in server_permissions
            or not self._server_has_webhook(server_id, webhook_id)
        ):
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
        if "trigger" in data.keys():
            triggers = ""
            for item in data["trigger"]:
                string = item + ","
                triggers += string
            data["trigger"] = triggers
        self.controller.management.modify_webhook(webhook_id, data)

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: updated webhook",
            server_id,
            self.get_remote_ip(),
        )

        self.finish_json(200, {"status": "ok"})

    def post(self, server_id: str, webhook_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            "Tested webhook",
            server_id,
            self.get_remote_ip(),
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
        if (
            EnumPermissionsServer.CONFIG not in server_permissions
            or not self._server_has_webhook(server_id, webhook_id)
        ):
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
        webhook = self.controller.management.get_webhook_by_id(webhook_id)
        webhook_provider = WebhookFactory.create_provider(webhook["webhook_type"])
        server_base_url = f"{self.helper.get_setting('base_url')}"
        dummy_backup_url = (
            f"https://{server_base_url}/api/v2/servers/{server_id}"
            "/backups/backup/0683f7fb-09a2-432f-9dd6-775019f75fae/"
            "download/2000-01-01_00-00-00.zip"
        )
        event_data = {
            "server_name": self.controller.servers.get_server_data_by_id(server_id)[
                "server_name"
            ],
            "server_id": server_id,
            "command": "/say This is a test command!",
            "event_type": "test_webhook",
            "backup_name": "2000-01-01_00-00-00.zip",
            "backup_size": "0MB",
            "backup_link": dummy_backup_url,
            "backup_status": "✅",
            "backup_error": "[Errno 2] This Backup is not real!",
        }

        event_data = webhook_provider.add_time_variables(event_data)

        try:

            webhook_provider.send(
                server_name=self.controller.servers.get_server_data_by_id(server_id)[
                    "server_name"
                ],
                title=f"Test Webhook: {webhook['name']}",
                url=webhook["url"],
                message_template=webhook["body"],
                event_data=event_data,
                color=webhook["color"],  # Prestigious purple!
                bot_name="Crafty Webhooks Tester",
            )
        except Exception as e:
            self.finish_json(
                500, {"status": "error", "error": "WEBHOOK ERROR", "error_data": str(e)}
            )

        self.finish_json(200, {"status": "ok"})
