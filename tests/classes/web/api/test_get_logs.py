from pathlib import Path
from unittest.mock import MagicMock as Mock

import pytest

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.shared.server import ServerOutBuf
from app.classes.web.routes.api.servers.server.logs import ApiServersServerLogsHandler


@pytest.mark.parametrize("has_server_access", [True, False])
@pytest.mark.parametrize("read_log_file", [True, False], ids=["file", "terminal"])
@pytest.mark.parametrize(
    "permissions",
    [
        [],
        [EnumPermissionsServer.LOGS],
        [EnumPermissionsServer.TERMINAL],
        [EnumPermissionsServer.LOGS, EnumPermissionsServer.TERMINAL],
    ],
    ids=["no-permissions", "logs-only", "terminal-only", "logs-and-terminal"],
)
def test_log_access_permissions(
    tmp_path, monkeypatch, has_server_access, read_log_file, permissions
) -> None:
    server_id = "test-server"
    auth_data = (
        [{"server_id": server_id}] if has_server_access else [],
        [],
        [],
        False,
        {"user_id": 1, "superuser": False, "lang": "en_EN"},
        "api-mask",
        None,
    )
    controller = Mock()
    controller.server_perms.get_permissions.return_value = permissions
    controller.servers.get_server_data_by_id.return_value = {
        "path": str(tmp_path),
        "log_path": "logs/latest.log",
    }
    handler = ApiServersServerLogsHandler.__new__(ApiServersServerLogsHandler)
    query = {"file": "true"} if read_log_file else {}
    handler.get_query_argument = Mock(side_effect=query.get)
    handler.controller = controller
    handler.helper = Mock()
    handler.helper.translation.translate.return_value = "Insufficient permissions"
    handler.helper.get_setting.return_value = 100
    log_path = tmp_path / "logs" / "latest.log"
    handler.helper.resolve_log_path.return_value = log_path
    handler.helper.tail_file.return_value = ["\x1b[32mfile <entry>\x1b[0m\r\n"]
    monkeypatch.setattr(ServerOutBuf, "lines", {server_id: ["terminal <entry>"]})
    handler.authenticate_user = Mock(return_value=auth_data)
    handler.finish_json = Mock()

    handler.get(server_id)

    controller.server_perms.get_user_permissions_mask.assert_called_once_with(
        1, server_id
    )
    controller.server_perms.get_lowest_api_perm_mask.assert_called_once_with(
        controller.server_perms.get_user_permissions_mask.return_value, "api-mask"
    )
    required_permission = (
        EnumPermissionsServer.LOGS if read_log_file else EnumPermissionsServer.TERMINAL
    )
    if has_server_access and required_permission in permissions:
        expected_line = (
            "file &lt;entry&gt;" if read_log_file else "terminal &lt;entry&gt;"
        )
        handler.finish_json.assert_called_once_with(
            200, {"status": "ok", "data": [expected_line]}
        )
        if read_log_file:
            handler.helper.resolve_log_path.assert_called_once_with(Path(log_path))
            handler.helper.tail_file.assert_called_once_with(log_path, 100)
        else:
            handler.helper.tail_file.assert_not_called()
    else:
        handler.finish_json.assert_called_once_with(
            403,
            {
                "status": "error",
                "error": "NOT_AUTHORIZED",
                "error_data": "Insufficient permissions",
            },
        )
        controller.servers.get_server_data_by_id.assert_not_called()
        handler.helper.tail_file.assert_not_called()
