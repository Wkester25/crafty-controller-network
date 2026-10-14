import pytest
from jsonschema import ValidationError, validate

from app.classes.remote_stats import ping as ping_module
from app.classes.remote_stats.stats import Stats
from app.classes.web.routes.api.servers.server.index import (
    basic_server_patch_schema,
    server_patch_schema,
)


@pytest.mark.parametrize(
    "server_type, expected",
    [
        ("minecraft-java", "java"),
        ("steam_cmd", "java"),
        ("minecraft-bedrock", "bedrock"),
        ("raknet", "raknet"),
        ("hytale", "nitrado"),
        ("something-new", "java"),
    ],
)
def test_auto_resolves_from_server_type(server_type, expected) -> None:
    assert ping_module.resolve_ping_mode(server_type, "auto") == expected
    assert ping_module.resolve_ping_mode(server_type) == expected


@pytest.mark.parametrize("mode", [m for m in ping_module.PING_MODES if m != "auto"])
@pytest.mark.parametrize("server_type", ["minecraft-java", "minecraft-bedrock"])
def test_explicit_mode_overrides_server_type(server_type, mode) -> None:
    assert ping_module.resolve_ping_mode(server_type, mode) == mode


def test_unknown_stored_mode_falls_back_to_auto() -> None:
    assert ping_module.resolve_ping_mode("minecraft-bedrock", "bogus") == "bedrock"


@pytest.mark.parametrize(
    "mode, target",
    [
        ("java", "ping"),
        ("raknet", "ping_raknet"),
        ("nethernet", "ping_nethernet"),
        ("bedrock", "ping_bedrock"),
    ],
)
def test_ping_server_dispatches_to_matching_ping(monkeypatch, mode, target) -> None:
    calls = []
    for name in ("ping", "ping_raknet", "ping_nethernet", "ping_bedrock"):
        monkeypatch.setattr(
            ping_module,
            name,
            lambda ip, port, name=name: calls.append((name, ip, port)) or name,
        )

    assert ping_module.ping_server(mode, "10.0.0.5", "19132") == target
    assert calls == [(target, "10.0.0.5", 19132)]


def test_ping_server_dispatches_nitrado(monkeypatch) -> None:
    calls = []

    def fake_ping(ip, port):
        calls.append((ip, port))
        return {"Server": {}}

    monkeypatch.setattr(ping_module.NitradoPing, "ping", fake_ping)

    assert ping_module.ping_server("nitrado", "10.0.0.5", "5520") == {"Server": {}}
    assert calls == [("10.0.0.5", 5520)]


def test_ping_server_java_swallows_os_error(monkeypatch) -> None:
    def refuse(*_args):
        raise ConnectionRefusedError()

    monkeypatch.setattr(ping_module, "ping", refuse)

    assert ping_module.ping_server("java", "127.0.0.1", 25565) is False


def test_ping_server_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        ping_module.ping_server("auto", "127.0.0.1", 25565)


@pytest.mark.parametrize(
    "mode, parser",
    [
        ("java", "parse_server_ping"),
        ("raknet", "parse_server_raknet_ping"),
        ("nethernet", "parse_server_raknet_ping"),
        ("bedrock", "parse_server_raknet_ping"),
    ],
)
def test_parse_ping_follows_mode(monkeypatch, mode, parser) -> None:
    monkeypatch.setattr(Stats, parser, staticmethod(lambda obj: ("parsed", obj)))

    assert Stats.parse_ping(mode, "reply") == ("parsed", "reply")


def test_parse_ping_nitrado() -> None:
    reply = {
        "Server": {"Name": "Hytale", "Version": "1.0", "MaxPlayers": 10},
        "Universe": {"CurrentPlayers": 2},
        "Players": ["a", "b"],
    }

    parsed = Stats.parse_ping("nitrado", reply)

    assert (parsed["online"], parsed["max"], parsed["players"]) == (2, 10, ["a", "b"])


def test_parse_ping_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        Stats.parse_ping("auto", {})


@pytest.mark.parametrize("mode", ping_module.PING_MODES)
def test_patch_schema_accepts_every_mode(mode) -> None:
    validate({"ping_mode": mode}, server_patch_schema)


@pytest.mark.parametrize("value", ["udp", "", 1, None])
def test_patch_schema_rejects_invalid_mode(value) -> None:
    with pytest.raises(ValidationError):
        validate({"ping_mode": value}, server_patch_schema)


def test_ping_mode_is_superuser_only() -> None:
    with pytest.raises(ValidationError):
        validate({"ping_mode": "java"}, basic_server_patch_schema)
