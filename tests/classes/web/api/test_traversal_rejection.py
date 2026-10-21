import json
from unittest.mock import MagicMock as Mock

from app.classes.helpers.file_helpers import FileHelpers
from app.classes.helpers.helpers import Helpers
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.web.routes.api.servers.server.files import (
    ApiServersServerFilesIndexHandler,
    ApiServersServerFilesOperationHandler,
)

SERVER = "server-1"
USER = {"user_id": 1, "username": "alice", "superuser": False, "lang": "en_EN"}


def make_handler(handler_cls, tmp_path, body):
    """Build an API handler with a mocked controller and request body."""
    handler = handler_cls.__new__(handler_cls)
    handler.controller = Mock()
    handler.controller.server_perms.get_permissions.return_value = [
        EnumPermissionsServer.FILES
    ]
    handler.controller.servers.get_server_data_by_id.return_value = {
        "path": str(tmp_path)
    }
    handler.helper = Mock(is_path_within=Helpers.is_path_within)
    handler.file_helper = Mock(get_absolute_path=FileHelpers.get_absolute_path)
    handler.request = Mock(body=json.dumps(body).encode())
    handler.authenticate_user = Mock(
        return_value=([{"server_id": SERVER}], [], [], False, USER, "mask")
    )
    handler.get_remote_ip = Mock(return_value="127.0.0.1")
    handler.finish_json = Mock()
    return handler


def assert_rejected(handler):
    """Assert exactly one TRAVERSAL DETECTED response was sent."""
    handler.finish_json.assert_called_once()
    status, payload = handler.finish_json.call_args.args
    assert status == 403
    assert payload["error"] == "TRAVERSAL DETECTED"


def test_reject_traversal_rejects_outside_path(tmp_path):
    handler = make_handler(BaseApiHandler, tmp_path, {})
    assert not handler.reject_traversal(str(tmp_path), str(tmp_path / ".." / "x"))
    assert_rejected(handler)


def test_delete_with_one_bad_path_deletes_nothing(tmp_path):
    inside = tmp_path / "keep.txt"
    inside.write_text("x", encoding="utf-8")
    body = {
        "file_system_objects": [
            {"filename": "keep.txt"},
            {"filename": "../outside.txt"},
        ]
    }
    handler = make_handler(ApiServersServerFilesIndexHandler, tmp_path, body)
    handler.controller.servers.get_server_instance_by_id.return_value = Mock(
        check_running=Mock(return_value=False)
    )

    handler.delete(SERVER)

    assert_rejected(handler)
    assert inside.exists()


def test_move_with_one_bad_path_moves_nothing(tmp_path):
    inside = tmp_path / "keep.txt"
    inside.write_text("x", encoding="utf-8")
    (tmp_path / "dest").mkdir()
    body = {
        "file_system_objects": [
            {"source_path": "keep.txt", "target_path": "dest"},
            {"source_path": "keep.txt", "target_path": "../outside"},
        ]
    }
    handler = make_handler(ApiServersServerFilesOperationHandler, tmp_path, body)

    handler.post(SERVER, "move")

    assert_rejected(handler)
    assert inside.exists()
    assert not (tmp_path / "dest" / "keep.txt").exists()
