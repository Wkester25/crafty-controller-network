import logging

import pytest

from app.classes.remote_stats import ping as ping_module


def _stub_raknet(monkeypatch, error: Exception) -> None:
    class FailingRaknetPing:
        def __init__(self, *_args, **_kwargs):
            pass

        def ping(self):
            raise error

    monkeypatch.setattr(ping_module, "RaknetPing", FailingRaknetPing)


@pytest.mark.parametrize(
    "error",
    [ConnectionResetError(10054, "reset"), TimeoutError(), ConnectionRefusedError()],
)
def test_ping_raknet_network_error_logs_warning_without_traceback(
    monkeypatch, caplog, error
) -> None:
    _stub_raknet(monkeypatch, error)

    with caplog.at_level(logging.WARNING, logger=ping_module.logger.name):
        assert ping_module.ping_raknet("127.0.0.1", 19132) is None

    records = [r for r in caplog.records if "RakNet" in r.getMessage()]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert records[0].exc_info is None


def test_ping_raknet_unexpected_error_keeps_traceback(monkeypatch, caplog) -> None:
    _stub_raknet(monkeypatch, RuntimeError("bug"))

    with caplog.at_level(logging.WARNING, logger=ping_module.logger.name):
        assert ping_module.ping_raknet("127.0.0.1", 19132) is None

    records = [r for r in caplog.records if "RakNet" in r.getMessage()]
    assert [r.levelno for r in records] == [logging.ERROR]
    assert records[0].exc_info is not None
