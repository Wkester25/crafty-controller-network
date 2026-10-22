import zipfile
import json
from pathlib import Path
from unittest.mock import MagicMock as Mock

from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.helpers.helpers import Helpers
from app.classes.web.routes.api.crafty.imports.index import ApiImportFilesIndexHandler


def test_import_scan_outside_zipfile(tmp_path) -> None:
    archive_path = Path(tmp_path, "import", "upload", "archive.zip")
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "w") as zip_ref:
        zip_ref.writestr("dir/ok.txt", "ok")
    auth_data = (
        [],
        [],
        [],
        False,
        {"user_id": 1, "superuser": False, "lang": "en_EN"},
        "",
        None,
    )

    controller = Mock()
    controller.project_root = tmp_path
    controller.crafty_perms = Mock()
    controller.crafty_perms.get_crafty_permissions_list.return_value = [
        EnumPermissionsCrafty.SERVER_CREATION
    ]

    handler = ApiImportFilesIndexHandler.__new__(ApiImportFilesIndexHandler)
    handler.request = Mock()
    handler.request.body = json.dumps({"file_name": "archive.zip", "local_path": ""})
    handler.controller = controller
    handler.helper = Mock()
    handler.helper.validate_traversal = Helpers.validate_traversal
    handler.authenticate_user = Mock(return_value=auth_data)
    handler.finish_json = Mock()

    handler.post()

    handler.finish_json.assert_called_once_with(
        200,
        {
            "status": "ok",
            "data": {
                "top": True,
                "request_path": "",
                "dir": {"path": "dir/", "dir": True},
            },
        },
    )
