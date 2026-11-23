import logging
import json
import zipfile
from pathlib import Path
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)
files_get_schema = {
    "type": "object",
    "properties": {
        "file_name": {"type": "string", "error": "typeString", "fill": True},
        "local_path": {"type": "string", "error": "typeString", "fill": True},
    },
    "additionalProperties": False,
    "minProperties": 1,
}


class ApiImportFilesIndexHandler(BaseApiHandler):
    def post(self):
        # Disable pylint. This is a constant variable
        IMPORT_PATH = Path(  # pylint: disable=invalid-name
            self.controller.project_root, "import", "upload"
        )
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        if (
            EnumPermissionsCrafty.SERVER_CREATION
            not in self.controller.crafty_perms.get_crafty_permissions_list(
                auth_data[4]["user_id"]
            )
            and not self.is_superuser(auth_data)
        ):
            # if the user doesn't have server creation, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": "INSUFFICEN PERMISSIONS",
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
        return_json = {
            "top": data["local_path"] == "",
            "request_path": data["local_path"],
        }
        zip_path = Path(IMPORT_PATH, data["file_name"]).resolve()

        if data["local_path"] != "":
            data["local_path"] += "/"

        try:  # Check Traversal On Zipfile local path
            self.helper.validate_traversal(
                IMPORT_PATH, zip_path
            )  # check file name traversal
        except ValueError as why:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "TRAVERSAL_DETECTED",
                    "error_data": str(why),
                },
            )
        try:
            path = zipfile.Path(zip_path, at=str(data["local_path"]))
        except zipfile.BadZipFile:
            return self.finish_json(
                500,
                {
                    "status": "error",
                    "error": "Invalid Or Corrupt File",
                    "error_data": self.helper.translation.translate(
                        "serverWizard", "zipError", auth_data[4]["lang"]
                    ),
                },
            )
        try:
            self.helper.validate_traversal(
                str(Path(IMPORT_PATH, data["file_name"])), str(path)
            )
        except ValueError as why:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "TRAVERSAL_DETECTED",
                    "error_data": str(why),
                },
            )
        for file in path.iterdir():
            if file.is_dir():
                return_json[file.name] = {
                    "path": str(file.at),
                    "dir": True,
                }
            else:
                return_json[file.name] = {
                    "path": str(file.at),
                    "dir": False,
                }
        return self.finish_json(200, {"status": "ok", "data": return_json})
