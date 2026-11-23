import os
import logging
import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

backup_patch_schema = {
    "type": "object",
    "properties": {
        "backup_name": {"type": "string", "minLength": 3, "error": "backupName"},
        "backup_location": {
            "type": "string",
            "minLength": 1,
            "error": "typeString",
            "fill": True,
        },
        "max_backups": {
            "type": "integer",
            "error": "typeInteger",
            "fill": True,
        },
        "compress": {
            "type": "boolean",
            "error": "typeBool",
            "fill": True,
        },
        "shutdown": {
            "type": "boolean",
            "error": "typeBool",
            "fill": True,
        },
        "before": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "after": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "excluded_dirs": {
            "type": "array",
            "error": "typeList",
            "fill": True,
        },
        "backup_type": {
            "type": "string",
            "enum": ["zip_vault", "snapshot"],
            "error": "enumErr",
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}

basic_backup_patch_schema = {
    "type": "object",
    "properties": {
        "backup_name": {"type": "string", "minLength": 3, "error": "backupName"},
        "max_backups": {
            "type": "integer",
            "error": "typeInt",
            "fill": True,
        },
        "compress": {
            "type": "boolean",
            "error": "typeBool",
            "fill": True,
        },
        "shutdown": {
            "type": "boolean",
            "error": "typeBool",
            "fill": True,
        },
        "before": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "after": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "excluded_dirs": {
            "type": "array",
            "error": "typeList",
            "fill": True,
        },
        "backup_type": {
            "type": "string",
            "enum": ["zip_vault", "snapshot"],
            "error": "enumErr",
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}


class ApiServersServerBackupsIndexHandler(BaseApiHandler):
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
        if EnumPermissionsServer.BACKUP not in server_permissions:
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
            200, self.controller.management.get_backups_by_server(server_id)
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
            if self.is_superuser(auth_data):
                validate(data, backup_patch_schema)
            else:
                validate(data, basic_backup_patch_schema)
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
        if EnumPermissionsServer.BACKUP not in server_permissions:
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
        # Set the backup location automatically for non-super users. We should probably
        # make the default location configurable for SU eventually
        if not self.is_superuser(auth_data):
            data["backup_location"] = os.path.join(self.helper.backup_path, server_id)
        data["server_id"] = server_id
        if not data.get("excluded_dirs", None):
            data["excluded_dirs"] = []
        self.controller.management.add_backup_config(data)
        return self.finish_json(200, {"status": "ok"})
