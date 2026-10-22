import json
import types

import pytest

from app.classes.network import network_manager as nm
from app.classes.network import upnp
from app.classes.network.cloudflare_api import (
    MANAGED_COMMENT,
    CloudflareApi,
    CloudflareError,
)


# ---------------------------------------------------------------- fakes
class FakeHelper:
    def __init__(self, config_dir):
        self.config_dir = str(config_dir)

    def get_setting(self, key, default=None):
        return {"https_port": 8443}.get(key, default)


class FakeServer:
    def __init__(self, name, server_type, port, running=True):
        self.name = name
        self.settings = {"type": server_type, "server_port": port, "server_name": name}
        self.running = running
        self.port = port

    def check_running(self):
        return self.running

    def _get_game_port(self):
        return self.port


def make_controller(servers):
    entries = [{"server_id": sid, "server_obj": srv} for sid, srv in servers.items()]
    return types.SimpleNamespace(servers=types.SimpleNamespace(servers_list=entries))


class FakeGateway:
    host = "192.168.1.1"
    local_ip = "192.168.1.50"

    def __init__(self, external_ip="203.0.113.7"):
        self.external_ip = external_ip
        self.mappings = {}

    def get_external_ip(self):
        return self.external_ip

    def add_mapping(self, port, proto, internal_port, description, lease):
        self.mappings[(port, proto)] = description
        return lease

    def delete_mapping(self, port, proto):
        self.mappings.pop((port, proto), None)


class FakeApi:
    records = {}
    ingress = None

    def __init__(self, token):
        self.token = token

    def find_zone(self, domain):
        return "zone1", "acct1"

    def upsert_record(self, zone_id, record):
        FakeApi.records[(record["name"], record["type"])] = record

    def delete_record(self, zone_id, name, rtype):
        FakeApi.records.pop((name, rtype), None)

    def put_tunnel_ingress(self, account_id, tunnel_id, ingress):
        FakeApi.ingress = ingress

    def get_or_create_tunnel(self, account_id, name):
        return "tunnel-123"

    def get_tunnel_token(self, account_id, tunnel_id):
        return "tok"


@pytest.fixture
def env(tmp_path, monkeypatch):
    gateway = FakeGateway()
    FakeApi.records = {}
    FakeApi.ingress = None
    monkeypatch.setattr(nm, "discover", lambda: gateway)
    monkeypatch.setattr(nm, "CloudflareApi", FakeApi)
    monkeypatch.setattr(nm, "get_public_ip", lambda: "203.0.113.7")
    servers = {
        "java1": FakeServer("Survival", "minecraft-java", 25565),
        "bed1": FakeServer("Bedrock", "minecraft-bedrock", 19132, running=False),
    }
    manager = nm.NetworkManager(FakeHelper(tmp_path), make_controller(servers))
    # Never actually launch cloudflared in tests
    manager.cloudflared.find_binary = lambda *_: None
    return manager, gateway, servers


# ---------------------------------------------------------------- tests
def test_parse_extra_ports():
    assert nm._parse_extra_ports("24454/udp, 8100") == [(24454, "UDP"), (8100, "TCP")]
    assert nm._parse_extra_ports("19132/both") == [(19132, "TCP"), (19132, "UDP")]
    assert nm._parse_extra_ports("") == []
    with pytest.raises(nm.NetworkConfigError):
        nm._parse_extra_ports("70000")
    with pytest.raises(nm.NetworkConfigError):
        nm._parse_extra_ports("25/icmp")


def test_update_config_validates(env):
    manager, _, _ = env
    with pytest.raises(nm.NetworkConfigError):
        manager.update_config({"servers": {"java1": {"subdomain": "Bad_Name!"}}})
    with pytest.raises(nm.NetworkConfigError):
        manager.update_config(
            {
                "cloudflare": {"panel_subdomain": "mc"},
                "servers": {"java1": {"subdomain": "mc"}},
            }
        )
    with pytest.raises(nm.NetworkConfigError):
        manager.update_config({"upnp": {"bogus": 1}})


def test_token_is_masked_and_kept(env):
    manager, _, _ = env
    manager.update_config({"cloudflare": {"api_token": "secret", "domain": "Example.COM."}})
    manager.update_config({"cloudflare": {"api_token": ""}})
    assert manager.config["cloudflare"]["api_token"] == "secret"
    assert manager.config["cloudflare"]["domain"] == "example.com"
    public = manager.public_config()
    assert "api_token" not in public["cloudflare"]
    assert public["cloudflare"]["api_token_set"] is True
    assert "state" not in public
    with open(manager.config_path, encoding="utf-8") as f:
        assert json.load(f)["cloudflare"]["api_token"] == "secret"


def test_upnp_opens_only_running_servers_and_closes(env):
    manager, gateway, servers = env
    manager.update_config(
        {
            "upnp": {"enabled": True},
            "servers": {
                "java1": {"upnp": True, "extra_ports": "24454/udp"},
                "bed1": {"upnp": True},
            },
        }
    )
    manager.sync()
    assert set(gateway.mappings) == {(25565, "TCP"), (24454, "UDP")}

    servers["bed1"].running = True
    manager.sync()
    assert (19132, "UDP") in gateway.mappings

    servers["java1"].running = False
    manager.sync()
    assert set(gateway.mappings) == {(19132, "UDP")}

    manager.update_config({"upnp": {"enabled": False}})
    manager.sync()
    assert gateway.mappings == {}
    assert manager.config["state"]["upnp"] == []


def test_cgnat_is_flagged(env):
    manager, gateway, _ = env
    gateway.external_ip = "100.72.1.2"
    manager.update_config({"upnp": {"enabled": True}, "servers": {"java1": {"upnp": True}}})
    manager.sync()
    assert manager.status["upnp"]["cgnat"] is True


def test_dns_records_tunnel_and_cleanup(env):
    manager, _, _ = env
    manager.update_config(
        {
            "cloudflare": {
                "api_token": "t",
                "domain": "example.com",
                "dns_enabled": True,
                "tunnel_enabled": True,
            },
            "servers": {
                "java1": {"subdomain": "survival", "dns": True},
                "bed1": {"subdomain": "bedrock", "dns": True},
            },
        }
    )
    manager.setup_tunnel()
    manager.sync()

    recs = FakeApi.records
    assert recs[("survival.example.com", "A")]["content"] == "203.0.113.7"
    assert recs[("survival.example.com", "A")]["proxied"] is False
    srv = recs[("_minecraft._tcp.survival.example.com", "SRV")]["data"]
    assert srv["port"] == 25565 and srv["target"] == "survival.example.com"
    assert ("bedrock.example.com", "A") in recs
    assert ("_minecraft._tcp.bedrock.example.com", "SRV") not in recs
    cname = recs[("crafty.example.com", "CNAME")]
    assert cname["content"] == "tunnel-123.cfargotunnel.com" and cname["proxied"]
    assert FakeApi.ingress[0]["hostname"] == "crafty.example.com"
    assert FakeApi.ingress[0]["service"] == "https://localhost:8443"
    assert FakeApi.ingress[-1] == {"service": "http_status:404"}

    # Renaming a subdomain removes the old records
    manager.update_config({"servers": {"java1": {"subdomain": "smp"}}})
    manager.sync()
    assert ("survival.example.com", "A") not in recs
    assert ("_minecraft._tcp.survival.example.com", "SRV") not in recs
    assert ("smp.example.com", "A") in recs

    # Deleting a server removes its records
    nm.NetworkManager.instance = manager
    nm.NetworkManager.forget_server("bed1")
    manager.sync()
    assert ("bedrock.example.com", "A") not in recs


def test_dns_kept_when_public_ip_unknown(env, monkeypatch):
    manager, _, _ = env
    manager.update_config(
        {
            "cloudflare": {"api_token": "t", "domain": "example.com", "dns_enabled": True},
            "servers": {"java1": {"subdomain": "survival", "dns": True}},
        }
    )
    manager.sync()
    assert ("survival.example.com", "A") in FakeApi.records

    def fail():
        raise CloudflareError("offline")

    monkeypatch.setattr(nm, "get_public_ip", fail)
    manager.sync(force=True)
    assert ("survival.example.com", "A") in FakeApi.records


def test_upnp_falls_back_to_permanent_lease(monkeypatch):
    calls = []

    def fake_soap(self, action, args):
        calls.append(args["NewLeaseDuration"])
        if args["NewLeaseDuration"]:
            raise upnp.UpnpError("OnlyPermanentLeasesSupported", "725")

    monkeypatch.setattr(upnp.UpnpGateway, "_detect_local_ip", lambda self: "10.0.0.2")
    monkeypatch.setattr(upnp.UpnpGateway, "_soap", fake_soap)
    gw = upnp.UpnpGateway("http://10.0.0.1:5000/desc.xml", "http://x/ctl", "svc")
    assert gw.add_mapping(25565, "TCP", 25565, "test", 3600) == 0
    assert calls == [3600, 0]


def test_upsert_refuses_foreign_records(monkeypatch):
    api = CloudflareApi("t")
    monkeypatch.setattr(
        api,
        "list_records",
        lambda *a: [{"id": "1", "content": "1.2.3.4", "comment": None}],
    )
    with pytest.raises(CloudflareError):
        api.upsert_record("z", {"type": "A", "name": "mc.example.com", "content": "5.6.7.8"})


def test_upsert_skips_unchanged_managed_record(monkeypatch):
    api = CloudflareApi("t")
    current = {"id": "1", "content": "5.6.7.8", "proxied": False, "comment": MANAGED_COMMENT}
    monkeypatch.setattr(api, "list_records", lambda *a: [current])
    monkeypatch.setattr(
        api, "_request", lambda *a, **k: pytest.fail("should not call the API")
    )
    assert (
        api.upsert_record(
            "z", {"type": "A", "name": "mc.example.com", "content": "5.6.7.8", "proxied": False}
        )
        is current
    )
