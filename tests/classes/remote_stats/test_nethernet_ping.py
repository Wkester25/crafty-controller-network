import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import psutil
import pytest

from app.classes.remote_stats import ping as ping_module
from app.classes.remote_stats.nethernet_ping import ping_nethernet
from app.classes.remote_stats.stats import Stats

BDS_JOIN_RESPONSE = {
    "name": "Nelson Family Creative Server",
    "protocol": 2193,
    "version": "1.26.52",
    "level": "Family World",
    "players": 1,
    "maxPlayers": 10,
    "gameType": 1,
}


@pytest.fixture
def bds_http():
    """A stand-in for a NetherNet BDS signaling port. Set .status and .body."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # pylint: disable=invalid-name
            if self.path != "/v1/join":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(server.body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.status = 200
    server.body = json.dumps(BDS_JOIN_RESPONSE).encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ping_nethernet_maps_join_response(bds_http) -> None:
    result = ping_nethernet("127.0.0.1", bds_http.server_port)

    assert result["server_motd"] == "Nelson Family Creative Server"
    assert result["server_version_name"] == "1.26.52"
    assert result["server_player_count"] == 1
    assert result["server_player_max"] == 10
    assert result["server_level_name"] == "Family World"


def test_ping_nethernet_result_fits_raknet_parser(bds_http) -> None:
    parsed = Stats.parse_server_raknet_ping(
        ping_nethernet("127.0.0.1", bds_http.server_port)
    )

    assert parsed["online"] == 1
    assert parsed["max"] == 10
    assert parsed["server_description"] == "Nelson Family Creative Server"
    assert parsed["server_version"] == "1.26.52"


def _time_wait_on_port(port: int) -> list:
    return [
        c
        for c in psutil.net_connections(kind="tcp")
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_TIME_WAIT
    ]


@pytest.mark.parametrize("status", [200, 404])
def test_ping_nethernet_leaves_no_time_wait_on_server_port(bds_http, status) -> None:
    # BDS closes the connection first, like this stub, so a normal close leaves
    # TIME_WAIT on the BDS port. BDS binds without SO_REUSEADDR, so on Linux a
    # restart within ~60 s of a poll then silently fails to listen.
    bds_http.status = status

    ping_nethernet("127.0.0.1", bds_http.server_port)

    assert not _time_wait_on_port(bds_http.server_port)


def test_ping_nethernet_none_on_non_200(bds_http) -> None:
    bds_http.status = 404

    assert ping_nethernet("127.0.0.1", bds_http.server_port) is None


@pytest.mark.parametrize("body", [b"not json", b'{"name": "missing fields"}', b"[]"])
def test_ping_nethernet_none_on_unexpected_body(bds_http, body) -> None:
    bds_http.body = body

    assert ping_nethernet("127.0.0.1", bds_http.server_port) is None


def test_ping_nethernet_none_when_port_closed() -> None:
    assert ping_nethernet("127.0.0.1", _closed_port(), timeout=1) is None


def test_ping_bedrock_prefers_nethernet(bds_http, monkeypatch) -> None:
    def fail_raknet(*_args):
        raise AssertionError("RakNet ping should not run for a NetherNet server")

    monkeypatch.setattr(ping_module, "ping_raknet", fail_raknet)

    result = ping_module.ping_bedrock("127.0.0.1", bds_http.server_port)

    assert result["server_player_count"] == 1


def test_ping_bedrock_falls_back_to_raknet(monkeypatch) -> None:
    raknet_result = {"server_player_count": "3", "server_player_max": "20"}
    monkeypatch.setattr(ping_module, "ping_raknet", lambda *_args: raknet_result)

    assert ping_module.ping_bedrock("127.0.0.1", _closed_port()) is raknet_result
