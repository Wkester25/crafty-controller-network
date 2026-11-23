import os
import logging
import json
import html
import threading
from shutil import Error as shutilError
from datetime import datetime
from pathlib import Path, PurePath
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.helpers.helpers import Helpers
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)
HUMAN_TIME_FORMAT = "%Y/%m/%d %H:%M"

files_get_schema = {
    "type": "object",
    "properties": {
        "page": {
            "type": "string",
            "minLength": 1,
            "error": "typeString",
            "fill": True,
        },
        "path": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "modified_epoch": {
            "type": "number",
            "error": "typeEpoch",
            "fill": True,
        },
    },
    "required": ["path"],
    "additionalProperties": False,
    "minProperties": 1,
}

files_patch_schema = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "contents": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "modified_epoch": {
            "type": "number",
            "error": "typeEpoch",
            "fill": True,
        },
        "overwrite": {
            "type": "boolean",
            "error": "typeBoolean",
            "fill": True,
        },
    },
    "required": ["path", "contents"],
    "additionalProperties": False,
    "minProperties": 1,
}

files_unzip_schema = {
    "type": "object",
    "properties": {
        "folder": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "proc_id": {
            "type": "string",
            "desc": "uuid",
            "error": "typeString",
            "fill": True,
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}

files_operation_schema = {
    "type": "object",
    "properties": {
        "file_system_objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "error": "typeString",
                        "fill": True,
                    },
                    "target_path": {
                        "type": "string",
                        "error": "typeString",
                        "fill": True,
                    },
                },
                "required": ["source_path", "target_path"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["file_system_objects"],
    "additionalProperties": False,
}

files_create_schema = {
    "type": "object",
    "properties": {
        "parent": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "name": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "directory": {
            "type": "boolean",
            "error": "typeBool",
            "fill": True,
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}

files_rename_schema = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
        "new_name": {
            "type": "string",
            "error": "typeString",
            "fill": True,
        },
    },
    "additionalProperties": False,
    "minProperties": 1,
}

file_delete_schema = {
    "type": "object",
    "properties": {
        "file_system_objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "error": "typeString",
                        "fill": True,
                    },
                },
                "required": ["filename"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["file_system_objects"],
    "additionalProperties": False,
}


class ApiServersServerFilesIndexHandler(BaseApiHandler):
    def post(self, server_id: str, backup_id=None):
        """API getter method to get a directory or file. This is a post due to the
        get methods not accepting any input.

        Will accept a directory or a file in the request schema.

        Args:
            server_id (str): server id from request
            backup_id (_type_, optional): _description_. Defaults to None.

        Raises:
            OSError: _description_

        Returns:
            _type_: _description_
        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
            EnumPermissionsServer.FILES not in server_permissions
            and EnumPermissionsServer.BACKUP not in server_permissions
        ):
            # if the user doesn't have Files or Backup permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_get_schema)
        except ValidationError as e:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(e),
                },
            )
        # Check for absolute or relative path. Absolute paths should be deprecated
        request_path = data["path"]
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        data["path"] = self.file_helper.get_absolute_path(server_path, data["path"])
        if not self.reject_traversal(server_path, data["path"]):
            return
        parent_modified = Path(data["path"]).stat().st_mtime
        if data.get("modified_epoch", 0.0) == parent_modified:
            # If the requested directory has not changed we'll just return a http 304
            self.set_status(304)
            return self.finish()
        if os.path.isdir(data["path"]):
            # TODO: limit some columns for specific permissions?
            folder = data["path"]
            return_json = {
                "root_path": {
                    "local_path": request_path,
                    "path": folder,
                    "top": data["path"]
                    == self.controller.servers.get_server_data_by_id(server_id)["path"],
                    "modified": parent_modified,
                }
            }

            dir_list = []
            unsorted_files = []
            file_list = os.listdir(folder)
            for item in file_list:
                if os.path.isdir(os.path.join(folder, item)):
                    dir_list.append(item)
                else:
                    unsorted_files.append(item)
            file_list = sorted(dir_list, key=str.casefold) + sorted(
                unsorted_files, key=str.casefold
            )
            for filename in file_list:
                raw_path = Path(folder, filename).resolve()
                lib_stat = Path(raw_path).stat()
                can_open, mime = self.file_helper.probably_can_open_file(str(raw_path))
                modified_time = datetime.fromtimestamp(lib_stat.st_mtime)
                permissions = {
                    "can_read": os.access(raw_path, os.R_OK),
                    "can_write": os.access(raw_path, os.W_OK),
                    "can_execute": os.access(raw_path, os.X_OK),
                }
                if backup_id:
                    if str(
                        raw_path
                    ) in self.controller.management.get_excluded_backup_dirs(backup_id):
                        if os.path.isdir(raw_path):
                            return_json[filename] = {
                                "path": str(PurePath(raw_path)),
                                "dir": True,
                                "excluded": True,
                            }
                        else:
                            try:
                                file_size = os.path.getsize(raw_path)
                            except (OSError, IOError):
                                file_size = 0
                            return_json[filename] = {
                                "path": str(PurePath(raw_path)),
                                "dir": False,
                                "excluded": True,
                                "size": Helpers.human_readable_file_size(file_size),
                            }
                    else:
                        if os.path.isdir(raw_path):
                            return_json[filename] = {
                                "path": str(PurePath(raw_path)),
                                "dir": True,
                                "excluded": False,
                            }
                        else:
                            try:
                                file_size = os.path.getsize(raw_path)
                            except (OSError, IOError):
                                file_size = 0
                            return_json[filename] = {
                                "path": str(PurePath(raw_path)),
                                "dir": False,
                                "excluded": False,
                                "size": Helpers.human_readable_file_size(file_size),
                            }
                else:
                    if os.path.isdir(raw_path):
                        return_json[filename] = {
                            "path": str(
                                PurePath.relative_to(
                                    PurePath(raw_path), PurePath(server_path)
                                )
                            ),
                            "dir": True,
                            "excluded": False,
                            "permissions": permissions,
                            "modified": modified_time.strftime(HUMAN_TIME_FORMAT),
                        }
                    else:
                        try:
                            file_size = os.path.getsize(raw_path)
                        except (OSError, IOError):
                            file_size = 0
                        return_json[filename] = {
                            "path": str(
                                PurePath.relative_to(
                                    PurePath(raw_path), PurePath(server_path)
                                )
                            ),
                            "dir": False,
                            "excluded": False,
                            "can_open": can_open,
                            "permissions": permissions,
                            "mime": mime,
                            "modified": modified_time.strftime(HUMAN_TIME_FORMAT),
                            "size": Helpers.human_readable_file_size(file_size),
                        }
            self.finish_json(200, {"status": "ok", "data": return_json})
        else:
            try:
                if Path(data["path"]).is_file():
                    can_open, mime = self.file_helper.probably_can_open_file(
                        data["path"]
                    )
                    modified_epoch = Path(data["path"]).stat().st_mtime
                    modified_time = datetime.fromtimestamp(modified_epoch)
                    try:
                        file_size = os.path.getsize(data["path"])
                    except (OSError, IOError):
                        file_size = 0
                    attributes = {
                        "mime": mime,
                        "modified": modified_time.strftime(HUMAN_TIME_FORMAT),
                        "size": Helpers.human_readable_file_size(file_size),
                        "modified_epoch": modified_epoch,
                    }
                    with open(data["path"], encoding="utf-8") as file:
                        file_contents = file.read()
                    self.finish_json(
                        200,
                        {
                            "status": "ok",
                            "data": {
                                "content": file_contents,
                                "attributes": attributes,
                            },
                        },
                    )
                else:
                    raise OSError("Item is not a valid File")
            except (UnicodeDecodeError, OSError) as ex:
                self.finish_json(
                    400,
                    {"status": "error", "error": "DECODE_ERROR", "error_data": str(ex)},
                )

    def do_delete(self, data, auth_data, server_id):
        """Deletes a file. Called by API request handler

        Args:
            data (_type_): API request data (already validated)
            auth_data (_type_): API auth data (already checked)
            server_id (_type_): API requested server ID.

        Returns:
            _type_: Error if there is one, or None if a response was already sent.
        """
        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        items = [
            (item, self.file_helper.get_absolute_path(server_path, item["filename"]))
            for item in data["file_system_objects"]
        ]
        for _, filename in items:
            if not self.reject_traversal(server_path, filename):
                return None
            if filename == server_path:
                self.finish_json(
                    403,
                    {
                        "status": "error",
                        "error": "TRAVERSAL DETECTED",
                        "error_data": str("Traversal"),
                    },
                )
                return None
        proc = False
        for item, filename in items:
            if os.path.isdir(filename):
                proc = FileHelpers.del_dirs(filename)
            else:
                proc = FileHelpers.del_file(filename)
            self.controller.management.add_to_audit_log(
                auth_data[4]["user_id"],
                f"Deleted item {item['filename']}",
                server_id,
                self.get_remote_ip(),
            )
        return proc

    def delete(self, server_id: str, _backup_id=None):
        """API request handler to delete a file or directory.

        Args:
            server_id (str): Requested server UUID.

        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, file_delete_schema)
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

        proc = self.do_delete(data, auth_data, server_id)
        if proc is None:
            return None
        # disabling pylint because return value could be truthy
        # but not a true boolean value
        if proc == True:  # pylint: disable=singleton-comparison
            return self.finish_json(200, {"status": "ok"})
        return self.finish_json(
            500, {"status": "error", "error": "SERVER RUNNING", "error_data": str(proc)}
        )

    def patch(self, server_id: str, _backup_id):
        """Replaces content of file with request content. Usually called when editing/
        modifying the content of a file.

        Args:
            server_id (str): Requested server UUID.

        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_patch_schema)
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
        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        data["path"] = self.file_helper.get_absolute_path(server_path, data["path"])
        if not self.reject_traversal(server_path, data["path"]):
            return None
        file_path = Helpers.get_os_understandable_path(data["path"])
        file_contents = data["contents"]
        if Path(data["path"]).stat().st_mtime > data.get(
            "modified_epoch", 1.5
        ) and not data.get("overwrite"):
            self.set_status(409)
            return self.finish()
        # Open the file in write mode and store the content in file_object
        with open(file_path, "w", encoding="utf-8") as file_object:
            file_object.write(file_contents)

        # Update file details
        modified_epoch = Path(data["path"]).stat().st_mtime
        modified_time = datetime.fromtimestamp(modified_epoch)
        try:
            file_size = os.path.getsize(data["path"])
        except (OSError, IOError):
            file_size = 0
        attributes = {
            "mime": self.file_helper.check_mime_types(data["path"]),
            "modified": modified_time.strftime(HUMAN_TIME_FORMAT),
            "size": Helpers.human_readable_file_size(file_size),
            "modified_epoch": modified_epoch,
        }

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Edited file {data['path']}",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(
            200, {"status": "ok", "data": {"attributes": attributes}}
        )


class ApiServersServerFilesCreateHandler(BaseApiHandler):
    def patch(self, server_id: str):
        """Renames a file or directory.

        Args:
            server_id (str): Requested server UUID.

        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_rename_schema)
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
        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        path = self.file_helper.get_absolute_path(server_path, data["path"])
        new_item_name = data["new_name"]
        new_item_path = os.path.join(os.path.split(path)[0], new_item_name)
        if not self.reject_traversal(server_path, path) or not self.reject_traversal(
            server_path, new_item_path
        ):
            return None
        if Helpers.check_path_exists(os.path.abspath(new_item_path)):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "FILE EXISTS",
                    "error_data": {},
                },
            )
        try:
            os.rename(path, new_item_path)
        except OSError as why:
            self.finish_json(
                500, {"status": "error", "error": "OSERROR", "error_data": str(why)}
            )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Renamed item {data['path']} to {new_item_name}",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(200, {"status": "ok"})

    def put(self, server_id: str):
        """Creates requested file or directory.

        Args:
            server_id (str): Requested server UUID.
        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_create_schema)
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
        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        file_path = self.file_helper.get_absolute_path(server_path, data["parent"])
        path = os.path.join(file_path, data["name"])
        if not self.reject_traversal(server_path, path):
            return None
        if Helpers.check_path_exists(os.path.abspath(path)):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "FILE EXISTS",
                    "error_data": "Item already exists in file tree",
                },
            )
        if data["directory"]:
            os.mkdir(path)
        else:
            # Create the file by opening it
            with open(path, "w", encoding="utf-8") as file_object:
                file_object.close()
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Created item {path}",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(200, {"status": "ok"})


class ApiServersServerFilesZipHandler(BaseApiHandler):
    def post(self, server_id: str):
        """Unzips a requested file.

        This process will send progress updates to the user on registered webhooks under
        the "zip_status" key.

        Args:
            server_id (str): Requested server UUID.

        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_unzip_schema)
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

        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        target_file = self.file_helper.get_absolute_path(server_path, data["folder"])
        user_id = auth_data[4]["user_id"]
        if not self.reject_traversal(server_path, target_file):
            return None
        if Helpers.check_file_exists(target_file):
            destination_path = Path(target_file).parents[0]
            # Unzip file can throw OSError here, but this was true before my changes.
            # Leaving this call site as-is for now but happy to change it if needed.
            unzip_thread = threading.Thread(
                target=self.file_helper.unzip_file,
                daemon=True,
                args=(target_file, destination_path, server_id),
                kwargs={"proc_id": data.get("proc_id")},
                name=f"{target_file}_unzip",
            )
            unzip_thread.start()
            self.controller.management.add_to_audit_log(
                auth_data[4]["user_id"],
                f"Unzipped file {target_file} in {data['folder']}",
                server_id,
                self.get_remote_ip(),
            )
            return self.finish_json(200, {"status": "ok"})

        if user_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "FILE_DOES_NOT_EXIST",
                    "error_data": str(e),
                },
            )


class ApiServersServerFileDownload(BaseApiHandler):
    async def get(self, server_id: str, encoded_file_path: str):
        """Async downloads the requested file or directory.
        If file is a directory storage is checked then it is zipped and downloaded.
        The zip file is deleted after the download completes.

        Args:
            server_id (str): Requested server UUID.

        """
        logger.debug(
            "Download file request received. server_id: %s, encoded file path: %s",
            server_id,
            encoded_file_path,
        )
        auth_data = self.authenticate_user()
        if not auth_data:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators",
                        "insufficientPerms",
                        self.helper.get_setting("language"),
                    ),
                },
            )

        filepath = html.unescape(encoded_file_path)
        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        file_path = Path(self.file_helper.get_absolute_path(server_path, filepath))

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
        if not self.reject_traversal(
            self.controller.servers.get_server_data_by_id(server_id)["path"],
            file_path,
        ):
            return None

        if not file_path.exists():
            return self.finish_json(
                404,
                {
                    "status": "error",
                    "error": "File not found",
                    "error_data": f"Path does not exist: {file_path}",
                },
            )
        directory_download = False
        download_path = file_path
        if file_path.is_dir():
            directory_download = True
            logger.info("Requested download is a directory. Zipping...%s", file_path)
            archive_path = Path(
                self.controller.project_root,
                "temp",
                str(auth_data[4]["user_id"]),
                file_path.name,
            )
            archive_path.parent.mkdir(parents=True, exist_ok=True)

            target_total_size = self.file_helper.get_dir_size(Path(download_path))
            free_drive_storage = self.file_helper.get_drive_free_space(
                Path(download_path)
            )
            if not self.file_helper.has_enough_storage(
                target_total_size, free_drive_storage
            ):
                return self.finish_json(
                    507,
                    {
                        "status": "error",
                        "error": "Out Of Space",
                        "error_data": "System Does Not Have enough space for download",
                    },
                )
            self.file_helper.make_archive(archive_path, file_path)
            download_path = archive_path.with_suffix(".zip")

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"started file download for {download_path} from server {server_id}.",
            server_id,
            self.get_remote_ip(),
        )
        await self.download_file(download_path)  # Make sure to check for permissions
        # and traversal before calling download. There is no permission checking
        # in this function
        if directory_download:
            os.remove(download_path)

        return None


class ApiServersServerFilesOperationHandler(BaseApiHandler):
    def do_operation(self, operation: str, source_path: Path, target_file: Path):
        if operation == "move":
            if Path(source_path).is_dir():
                FileHelpers.move_dir(source_path, target_file)
            else:
                FileHelpers.move_file(source_path, target_file)
        elif operation == "copy":
            if Path(source_path).is_dir():
                FileHelpers.copy_dir(source_path, target_file)
            else:
                FileHelpers.copy_file(source_path, target_file)

    def resolve_operation_paths(self, server_path: str, items: list):
        """Resolve and validate the source and target of every requested item.

        Every pair is checked before any is returned, so one bad path rejects
        the whole request.

        Args:
            server_path (str): Absolute path of the server directory.
            items (list): ``file_system_objects`` entries from the request body.

        Returns:
            list | None: ``(source_path, target_path)`` pairs, or ``None`` if a
            path escapes the server directory and a 403 was already sent.
        """
        paths = []
        for item in items:
            source_path = Path(
                self.file_helper.get_absolute_path(server_path, item["source_path"])
            )
            target_path = Path(
                self.file_helper.get_absolute_path(
                    server_path,
                    Path(item["target_path"], Path(source_path).name),
                )
            )
            paths.append((source_path, target_path))

        for source_path, target_path in paths:
            if not self.reject_traversal(
                server_path, source_path
            ) or not self.reject_traversal(server_path, target_path):
                return None
        return paths

    def post(self, server_id: str, operation: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

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
        if EnumPermissionsServer.FILES not in server_permissions:
            # if the user doesn't have Files permission, return an error
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
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, files_operation_schema)
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

        # Check for absolute or relative path. Absolute paths should be deprecated
        server_path = self.controller.servers.get_server_data_by_id(server_id)["path"]
        paths = self.resolve_operation_paths(server_path, data["file_system_objects"])
        if paths is None:
            return None

        for source_path, target_path in paths:
            try:
                self.do_operation(operation, source_path, target_path)
            except shutilError as why:
                return self.finish_json(
                    500, {"status": "error", "error": "OSERROR", "error_data": str(why)}
                )
            self.controller.management.add_to_audit_log(
                auth_data[4]["user_id"],
                f"{operation} item from {source_path} to {target_path}.",
                server_id,
                self.get_remote_ip(),
            )
        return self.finish_json(200, {"status": "ok"})
