import logging
import json
import os
from pathlib import Path
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

BACKUP_SCHEMA = {
    "type": "object",
    "properties": {
        "filename": {
            "type": "string",
            "minLength": 5,
            "error": "typeString",
            "fill": True,
        },
        "inPlace": {"type": "boolean", "error": "typeBool", "fill": True},
    },
    "additionalProperties": False,
    "minProperties": 1,
}
BACKUP_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "backup_name": {
            "type": "string",
            "minLength": 3,
            "error": "backupName",
        },
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

BASIC_BACKUP_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "backup_name": {"type": "string", "minLength": 3, "error": "backupName"},
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
ID_MISMATCH = "Server ID backup server ID different"
GENERAL_AUTH_ERROR = "Authorization Error"


class ApiServersServerBackupsBackupIndexHandler(BaseApiHandler):
    def get(self, server_id: str, backup_id: str):
        auth_data = self.authenticate_user()
        backup_conf = self.controller.management.get_backup_config(backup_id)
        if not auth_data:
            return
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        if backup_conf["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.BACKUP not in server_permissions:
            # if the user doesn't have Schedule permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        self.finish_json(200, backup_conf)

    def delete(self, server_id: str, backup_id: str):
        auth_data = self.authenticate_user()
        backup_conf = self.controller.management.get_backup_config(backup_id)
        if backup_conf["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )
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
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: removed backup config"
            f" {backup_conf['backup_name']}",
            server_id,
            self.get_remote_ip(),
        )
        if backup_conf["default"]:
            return self.finish_json(
                405,
                {
                    "status": "error",
                    "error": "NOT_ALLOWED",
                    "error_data": "Cannot delete default backup",
                },
            )
        self.controller.management.delete_backup_config(backup_id)

        return self.finish_json(200, {"status": "ok"})

    def post(self, server_id: str, backup_id: str):
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
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        backup_config = self.controller.management.get_backup_config(backup_id)
        if backup_config["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, BACKUP_SCHEMA)
        except ValidationError as e:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(e),
                },
            )
        svr_obj = self.controller.servers.get_server_instance_by_id(server_id)
        svr_obj.server_restore_threader(
            backup_id, data["filename"], data.get("inPlace")
        )

        return self.finish_json(200, {"status": "ok"})

    def patch(self, server_id: str, backup_id: str):
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
                validate(data, BACKUP_PATCH_SCHEMA)
            else:
                validate(data, BASIC_BACKUP_PATCH_SCHEMA)
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
        backup_conf = self.controller.management.get_backup_config(backup_id)
        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            # if the user doesn't have access to the server, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        if backup_conf["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
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
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        self.controller.management.update_backup_config(backup_id, data)
        return self.finish_json(200, {"status": "ok"})


class ApiServersServerBackupsBackupFilesIndexHandler(BaseApiHandler):
    def delete(self, server_id: str, backup_id: str):
        auth_data = self.authenticate_user()
        backup_conf = self.controller.management.get_backup_config(backup_id)
        if backup_conf["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )
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
            # if the user doesn't have backup permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, BACKUP_SCHEMA)
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
        self.helper.validate_traversal(
            os.path.join(backup_conf["backup_location"], backup_conf["backup_id"]),
            os.path.join(
                backup_conf["backup_location"],
                backup_conf["backup_id"],
                data["filename"],
            ),
        )
        try:
            FileHelpers.del_file(
                os.path.join(
                    backup_conf["backup_location"],
                    backup_conf["backup_id"],
                    data["filename"],
                )
            )
        except Exception as e:
            return self.finish_json(
                400, {"status": "error", "error": "DELETE FAILED", "error_data": e}
            )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited server {server_id}: removed backup {data['filename']}",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(200, {"status": "ok"})

    def get(self, server_id: str, backup_id: str):
        auth_data = self.authenticate_user()
        backup_conf = self.controller.management.get_backup_config(backup_id)
        if backup_conf["server_id"]["server_id"] != server_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )
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
            # if the user doesn't have backup permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        backup_list = list(
            map(
                str,
                Path(backup_conf["backup_location"], backup_conf["backup_id"]).glob(
                    "*.zip"
                ),
            )
        )
        return self.finish_json(
            200,
            {"total": len(backup_list), "backups": backup_list},
        )
