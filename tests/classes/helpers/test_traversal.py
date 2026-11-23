import zipfile
import json
from pathlib import Path
from unittest.mock import MagicMock as Mock

import pytest

from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.helpers.helpers import Helpers
from app.classes.web.routes.api.crafty.imports.index import ApiImportFilesIndexHandler


def test_validate_traversal_allows_child_path(tmp_path) -> None:
    base = tmp_path / "base"
    allowed = base / "child" / "file.txt"

    assert Helpers.validate_traversal(base, allowed) == allowed.resolve()


def test_validate_traversal_rejects_parent_path(tmp_path) -> None:
    base = tmp_path / "base"
    bad_relative = Path("..") / "outside.txt"

    with pytest.raises(ValueError):
        Helpers.validate_traversal(base, bad_relative)


def test_validate_traversal_rejects_absolute_path(tmp_path) -> None:
    base = tmp_path / "base"
    bad_absolute = (tmp_path.parent / "outside.txt").resolve()

    with pytest.raises(ValueError):
        Helpers.validate_traversal(base, bad_absolute)


def test_unzip_file_rejects_parent_segments(tmp_path, monkeypatch) -> None:
    archive_path = tmp_path / "archive.zip"
    destination = tmp_path / "dest"

    with zipfile.ZipFile(archive_path, "w") as zip_ref:
        zip_ref.writestr("../outside.txt", "nope")
        zip_ref.writestr("dir/ok.txt", "ok")

    helper = Mock(spec=Helpers)
    helper.validate_traversal = Helpers.validate_traversal
    file_helper = FileHelpers(helper)
    monkeypatch.setattr(file_helper, "send_percentage", lambda *args, **kwargs: None)

    file_helper.unzip_file(
        str(archive_path),
        str(destination),
        user_id=["test"],
    )
    assert not (destination / "outside.txt").exists()
    # Extraction stops at the traversal entry, before the subsequent safe entry.
    assert not (destination / "dir" / "ok.txt").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_unzip_file_extracts_safe_entries(tmp_path, monkeypatch) -> None:
    archive_path = tmp_path / "archive.zip"
    destination = tmp_path / "dest"
    with zipfile.ZipFile(archive_path, "w") as zip_ref:
        zip_ref.writestr("dir/", "")
        zip_ref.writestr("dir/ok.txt", "ok")

    helper = Mock(spec=Helpers)
    helper.validate_traversal = Helpers.validate_traversal
    file_helper = FileHelpers(helper)
    monkeypatch.setattr(file_helper, "send_percentage", lambda *args, **kwargs: None)

    file_helper.unzip_file(str(archive_path), str(destination), user_id=["test"])

    assert (destination / "dir" / "ok.txt").read_text() == "ok"


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
    handler.request.body = json.dumps({"file_name": "archive.zip", "local_path": ".."})
    handler.controller = controller
    handler.helper = Mock()
    handler.helper.validate_traversal = Helpers.validate_traversal
    handler.authenticate_user = Mock(return_value=auth_data)
    handler.finish_json = Mock()

    handler.post()

    handler.finish_json.assert_called_once_with(
        403,
        {
            "status": "error",
            "error": "TRAVERSAL_DETECTED",
            "error_data": "Path traversal detected",
        },
    )
