# TODO: read and delete

import json
import logging

from croniter import croniter
from jsonschema import ValidationError, validate
from app.classes.models.server_permissions import EnumPermissionsServer

from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

task_patch_schema = {
    "type": "object",
    "properties": {
        "enabled": {
            "type": "boolean",
            "default": True,
            "error": "typeBool",
            "fill": True,
        },
        "action": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "action_id": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "interval": {
            "type": "integer",
            "error": "typeInteger",
            "fill": True,
        },
        "interval_type": {
            "type": "string",
            "enum": [
                # Basic tasks
                "hours",
                "minutes",
                "days",
                # Chain reaction tasks:
                "reaction",
                # CRON tasks:
                "",
            ],
            "error": "enumErr",
            "fill": True,
        },
        "name": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "start_time": {
            "type": "string",
            "pattern": r"\d{1,2}:\d{1,2}",
            "error": "typeString",
            "fill": True,
        },
        "command": {
            "type": ["string", "null"],
            "error": "typeString",
            "fill": True,
        },
        "one_time": {
            "type": "boolean",
            "default": False,
            "error": "typeBool",
            "fill": True,
        },
        "cron_string": {
            "type": "string",
            "default": "",
            "error": "typeString",
            "fill": True,
        },
        "parent": {
            "type": ["integer", "null"],
            "error": "typeInteger",
            "fill": True,
        },
        "delay": {
            "type": "integer",
            "default": 0,
            "error": "typeInteger",
            "fill": True,
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}


class ApiServersServerTasksTaskIndexHandler(BaseApiHandler):
    def get(self, server_id: str, task_id: str):
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
        if EnumPermissionsServer.SCHEDULE not in server_permissions:
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
        self.finish_json(200, self.controller.management.get_scheduled_task(task_id))

    def delete(self, server_id: str, task_id: str):
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
        if EnumPermissionsServer.SCHEDULE not in server_permissions:
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
            self.tasks_manager.remove_job(task_id)
        except Exception as why:
            return self.finish_json(
                400,
                {"status": "error", "error": "NO SCHEDULE FOUND", "error_data": why},
            )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: removed schedule",
            server_id,
            self.get_remote_ip(),
        )
        self.tasks_manager.reload_schedule_from_db()

        return self.finish_json(200, {"status": "ok"})

    def patch(self, server_id: str, task_id: str):
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
            validate(data, task_patch_schema)
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
        if EnumPermissionsServer.SCHEDULE not in server_permissions:
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

        # Checks to make sure some doofus didn't actually make the newly
        # created task a child of itself.
        if str(data.get("parent")) == str(task_id) and data.get("parent") is not None:
            data["parent"] = None

        data["server_id"] = server_id
        if "cron_string" in data:
            if data["cron_string"] != "" and not croniter.is_valid(data["cron_string"]):
                return self.finish_json(
                    405,
                    {
                        "status": "error",
                        "error": self.helper.translation.translate(
                            "error",
                            "cronFormat",
                            self.controller.users.get_user_lang_by_id(
                                auth_data[4]["user_id"]
                            ),
                        ),
                    },
                )
        self.tasks_manager.update_job(task_id, data)

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: updated schedule",
            server_id,
            self.get_remote_ip(),
        )
        self.tasks_manager.reload_schedule_from_db()

        self.finish_json(200, {"status": "ok"})
