"""Keeps router port forwards (UPnP), Cloudflare DNS records and the local
cloudflared tunnel in line with Crafty's network settings.

Model:
    * The Crafty web UI is published through a Cloudflare Tunnel on
      ``<panel_subdomain>.<domain>`` (HTTP, so any browser can reach it).
    * Each game server can get ``<subdomain>.<domain>``: UPnP opens its port on
      the router and a DNS-only A record (+ SRV for Minecraft Java) points the
      name at this network's public IP. Game traffic does not go through the
      tunnel, because Cloudflare only proxies arbitrary TCP/UDP for clients
      that run cloudflared themselves.

A background thread reconciles the desired state every minute (and right away
when settings change or a server starts/stops). Everything Crafty creates is
tracked in ``network.json`` so it can be removed again cleanly.
"""

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
import typing as t

from app.classes.network.cloudflare_api import (
    CloudflareApi,
    CloudflareError,
    get_public_ip,
)
from app.classes.network.cloudflared import CloudflaredProcess
from app.classes.network.upnp import UpnpError, UpnpGateway, discover, is_private_ip
from app.classes.shared.console import Console

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 60
SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
PROTOCOLS_BY_TYPE = {
    "minecraft-java": ["TCP"],
    "minecraft-bedrock": ["UDP"],
    "hytale": ["UDP"],
    "steam_cmd": ["TCP", "UDP"],
}
PROTOCOL_OVERRIDES = {"tcp": ["TCP"], "udp": ["UDP"], "both": ["TCP", "UDP"]}

DEFAULT_CONFIG: t.Dict[str, t.Any] = {
    "upnp": {
        "enabled": False,
        # Close a server's ports while it is stopped
        "only_while_running": True,
        "lease_seconds": 3600,
    },
    "cloudflare": {
        "api_token": "",
        "domain": "",
        "zone_id": "",
        "account_id": "",
        "tunnel_name": "crafty",
        "tunnel_id": "",
        "tunnel_enabled": False,
        "panel_subdomain": "crafty",
        "dns_enabled": False,
        "cloudflared_path": "",
    },
    # server_id -> {"subdomain", "upnp", "dns", "protocol", "port", "extra_ports"}
    "servers": {},
    # Bookkeeping of what Crafty created - not user editable
    "state": {"upnp": [], "dns": [], "dns_signature": "", "ingress_signature": ""},
}
DEFAULT_SERVER = {
    "subdomain": "",
    "upnp": False,
    "dns": False,
    "protocol": "auto",
    "port": 0,
    "extra_ports": "",
}


class NetworkConfigError(ValueError):
    pass


class NetworkManager:
    instance: t.Optional["NetworkManager"] = None

    def __init__(self, helper, controller):
        self.helper = helper
        self.controller = controller
        self.config_path = os.path.join(helper.config_dir, "network.json")
        self.cloudflared = CloudflaredProcess(os.path.join(helper.config_dir, "bin"))
        self.config = self._load()
        self.gateway: t.Optional[UpnpGateway] = None
        self.status: t.Dict[str, t.Any] = {
            "last_sync": None,
            "public_ip": None,
            "upnp": {"error": None},
            "cloudflare": {"error": None},
            "servers": {},
        }
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = False
        self._thread: t.Optional[threading.Thread] = None
        NetworkManager.instance = self

    # ================================================================ config
    def _load(self) -> dict:
        config = copy.deepcopy(DEFAULT_CONFIG)
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            for key in ("upnp", "cloudflare", "state"):
                config[key].update(stored.get(key, {}))
            config["servers"] = stored.get("servers", {})
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Could not read {self.config_path}: {e}")
        return config

    def _save(self) -> None:
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)
        os.replace(tmp, self.config_path)
        if os.name != "nt":
            # Holds the Cloudflare API token
            os.chmod(self.config_path, 0o600)

    def public_config(self) -> dict:
        """Config safe to send to the browser (token masked)."""
        with self._lock:
            config = copy.deepcopy(self.config)
        token = config["cloudflare"].pop("api_token")
        config["cloudflare"]["api_token_set"] = bool(token)
        config.pop("state")
        return config

    def update_config(self, data: dict) -> None:
        with self._lock:
            new = copy.deepcopy(self.config)
            for section in ("upnp", "cloudflare"):
                for key, value in data.get(section, {}).items():
                    if key not in DEFAULT_CONFIG[section]:
                        raise NetworkConfigError(f"Unknown setting {section}.{key}")
                    if key == "api_token" and value == "":
                        continue  # empty means "keep the current token"
                    new[section][key] = value

            cf = new["cloudflare"]
            cf["domain"] = cf["domain"].strip().lower().strip(".")
            cf["panel_subdomain"] = cf["panel_subdomain"].strip().lower()
            if cf["panel_subdomain"] and not SUBDOMAIN_RE.match(cf["panel_subdomain"]):
                raise NetworkConfigError("Invalid web UI subdomain")
            if cf["domain"] != self.config["cloudflare"]["domain"]:
                # A different domain means a different zone; force re-setup
                cf["zone_id"] = cf["account_id"] = ""

            for server_id, settings in data.get("servers", {}).items():
                merged = {**DEFAULT_SERVER, **new["servers"].get(server_id, {})}
                merged.update(
                    {k: v for k, v in settings.items() if k in DEFAULT_SERVER}
                )
                merged["subdomain"] = str(merged["subdomain"]).strip().lower()
                if merged["subdomain"] and not SUBDOMAIN_RE.match(merged["subdomain"]):
                    raise NetworkConfigError(
                        f"Invalid subdomain '{merged['subdomain']}' (use a-z, 0-9, -)"
                    )
                if merged["protocol"] not in ("auto", *PROTOCOL_OVERRIDES):
                    raise NetworkConfigError("Protocol must be auto, tcp, udp or both")
                merged["port"] = int(merged["port"] or 0)
                _parse_extra_ports(merged["extra_ports"])  # validates
                new["servers"][server_id] = merged

            self._check_duplicate_subdomains(new)
            self.config = new
            self._save()
        self.request_sync()

    def _check_duplicate_subdomains(self, config: dict) -> None:
        seen = {config["cloudflare"]["panel_subdomain"]}
        for settings in config["servers"].values():
            sub = settings.get("subdomain")
            if not sub:
                continue
            if sub in seen:
                raise NetworkConfigError(f"Subdomain '{sub}' is used more than once")
            seen.add(sub)

    # ============================================================ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="network_manager"
        )
        self._thread.start()

    def request_sync(self) -> None:
        self._wake.set()

    @classmethod
    def notify_server_change(cls) -> None:
        """Called by server instances when they start or stop."""
        if cls.instance:
            cls.instance.request_sync()

    def _loop(self) -> None:
        # Give servers a moment to auto-start before the first pass
        self._wake.wait(10)
        while not self._stopping:
            self._wake.clear()
            try:
                self.sync()
            except Exception as e:  # never let the thread die
                logger.exception(f"Network sync failed: {e}")
            self._wake.wait(SYNC_INTERVAL_SECONDS)

    def shutdown(self) -> None:
        """Stops the tunnel and removes the router port forwards Crafty made."""
        self._stopping = True
        self._wake.set()
        self.cloudflared.stop()
        with self._lock:
            if self.config["state"]["upnp"]:
                try:
                    gateway = self.gateway or discover()
                    for mapping in self.config["state"]["upnp"]:
                        gateway.delete_mapping(mapping["port"], mapping["proto"])
                    self.config["state"]["upnp"] = []
                    self._save()
                except (UpnpError, OSError) as e:
                    logger.warning(f"Could not remove UPnP mappings on exit: {e}")

    # ================================================================== sync
    def sync(self, force: bool = False) -> None:
        with self._lock:
            servers = self._collect_servers()
            self.status["servers"] = {
                sid: {"errors": []} for sid in self.config["servers"]
            }
            self._sync_upnp(servers)
            self._sync_cloudflare(servers, force)
            self._ensure_tunnel_process()
            self.status["last_sync"] = time.time()
            self._save()

    def _collect_servers(self) -> t.Dict[str, dict]:
        """Live info for every server that has network settings."""
        result = {}
        for entry in self.controller.servers.servers_list:
            server_id = str(entry["server_id"])
            settings = self.config["servers"].get(server_id)
            if not settings:
                continue
            srv = entry["server_obj"]
            server_type = srv.settings.get("type", "")
            protocols = PROTOCOL_OVERRIDES.get(
                settings["protocol"], PROTOCOLS_BY_TYPE.get(server_type, ["TCP"])
            )
            try:
                game_port = settings["port"] or srv._get_game_port()
            except (KeyError, TypeError, ValueError):
                game_port = srv.settings.get("server_port")
            ports = [(game_port, proto) for proto in protocols]
            ports += _parse_extra_ports(settings["extra_ports"])
            result[server_id] = {
                "name": srv.name or srv.settings.get("server_name", server_id),
                "type": server_type,
                "running": bool(srv.check_running()),
                "game_port": game_port,
                "ports": ports,
                "settings": settings,
            }
        return result

    @classmethod
    def forget_server(cls, server_id: str) -> None:
        """Called when a server is deleted; its forwards/records go next sync."""
        self = cls.instance
        if not self:
            return
        with self._lock:
            if self.config["servers"].pop(str(server_id), None) is not None:
                self._save()
        self.request_sync()

    def _server_error(self, server_id: str, message: str) -> None:
        self.status["servers"].setdefault(server_id, {"errors": []})
        self.status["servers"][server_id]["errors"].append(message)

    # ------------------------------------------------------------------ UPnP
    def _sync_upnp(self, servers: t.Dict[str, dict]) -> None:
        upnp_cfg = self.config["upnp"]
        tracked: t.List[dict] = self.config["state"]["upnp"]
        desired: t.Dict[t.Tuple[int, str], str] = {}
        if upnp_cfg["enabled"]:
            for server_id, info in servers.items():
                if not info["settings"]["upnp"]:
                    continue
                if upnp_cfg["only_while_running"] and not info["running"]:
                    continue
                for port, proto in info["ports"]:
                    desired[(int(port), proto)] = server_id

        if not desired and not tracked:
            self.status["upnp"] = {"enabled": upnp_cfg["enabled"], "error": None}
            return

        try:
            if self.gateway is None:
                self.gateway = discover()
            external_ip = self.gateway.get_external_ip()
        except (UpnpError, OSError) as e:
            self.gateway = None
            self.status["upnp"] = {"enabled": upnp_cfg["enabled"], "error": str(e)}
            return

        self.status["upnp"] = {
            "enabled": upnp_cfg["enabled"],
            "error": None,
            "router": self.gateway.host,
            "local_ip": self.gateway.local_ip,
            "external_ip": external_ip,
            "cgnat": is_private_ip(external_ip),
        }

        # Remove forwards that are no longer wanted
        keep = []
        for mapping in tracked:
            if (mapping["port"], mapping["proto"]) in desired:
                keep.append(mapping)
                continue
            try:
                self.gateway.delete_mapping(mapping["port"], mapping["proto"])
                logger.info(f"UPnP: closed {mapping['port']}/{mapping['proto']}")
            except UpnpError as e:
                logger.warning(f"UPnP: could not remove {mapping}: {e}")
                keep.append(mapping)
        tracked[:] = keep

        # Add new forwards and refresh leases that are halfway to expiry
        now = time.time()
        by_key = {(m["port"], m["proto"]): m for m in tracked}
        for (port, proto), server_id in desired.items():
            mapping = by_key.get((port, proto))
            if mapping and (
                mapping["lease"] == 0 or now - mapping["refreshed"] < mapping["lease"] / 2
            ):
                continue
            try:
                lease = self.gateway.add_mapping(
                    port,
                    proto,
                    port,
                    f"Crafty {servers[server_id]['name']}"[:60],
                    int(upnp_cfg["lease_seconds"]),
                )
            except UpnpError as e:
                self._server_error(server_id, f"UPnP {port}/{proto}: {e}")
                continue
            if mapping:
                mapping.update(lease=lease, refreshed=now)
            else:
                logger.info(f"UPnP: opened {port}/{proto} -> {self.gateway.local_ip}")
                tracked.append(
                    {
                        "port": port,
                        "proto": proto,
                        "server_id": server_id,
                        "lease": lease,
                        "refreshed": now,
                    }
                )
        self.status["upnp"]["mappings"] = tracked

    # ------------------------------------------------------------ Cloudflare
    def _cf_api(self) -> t.Optional[CloudflareApi]:
        cf = self.config["cloudflare"]
        if not cf["api_token"] or not cf["domain"]:
            return None
        api = CloudflareApi(cf["api_token"])
        if not cf["zone_id"]:
            cf["zone_id"], cf["account_id"] = api.find_zone(cf["domain"])
        return api

    def _desired_dns(self, servers: t.Dict[str, dict], public_ip: str) -> t.List[dict]:
        cf = self.config["cloudflare"]
        domain = cf["domain"]
        records = []
        if cf["tunnel_enabled"] and cf["tunnel_id"] and cf["panel_subdomain"]:
            records.append(
                {
                    "type": "CNAME",
                    "name": f"{cf['panel_subdomain']}.{domain}",
                    "content": f"{cf['tunnel_id']}.cfargotunnel.com",
                    "proxied": True,
                }
            )
        if cf["dns_enabled"] and public_ip:
            for info in servers.values():
                settings = info["settings"]
                if not (settings["dns"] and settings["subdomain"]):
                    continue
                host = f"{settings['subdomain']}.{domain}"
                records.append(
                    {"type": "A", "name": host, "content": public_ip, "proxied": False}
                )
                if info["type"] == "minecraft-java":
                    # Lets players join with just the hostname, no :port
                    records.append(
                        {
                            "type": "SRV",
                            "name": f"_minecraft._tcp.{host}",
                            "data": {
                                "priority": 0,
                                "weight": 5,
                                "port": int(info["game_port"]),
                                "target": host,
                            },
                        }
                    )
        return records

    def _sync_cloudflare(self, servers: t.Dict[str, dict], force: bool) -> None:
        cf = self.config["cloudflare"]
        state = self.config["state"]
        status = {"error": None}
        self.status["cloudflare"] = status
        try:
            api = self._cf_api()
        except CloudflareError as e:
            status["error"] = str(e)
            return
        if api is None:
            return

        public_ip = None
        if cf["dns_enabled"]:
            try:
                public_ip = get_public_ip()
            except CloudflareError as e:
                status["error"] = str(e)
        self.status["public_ip"] = public_ip

        # ---- tunnel ingress (web UI)
        if cf["tunnel_enabled"] and cf["tunnel_id"]:
            ingress = []
            if cf["panel_subdomain"]:
                ingress.append(
                    {
                        "hostname": f"{cf['panel_subdomain']}.{cf['domain']}",
                        "service": f"https://localhost:{self.helper.get_setting('https_port', 8443)}",
                        # Crafty uses a self-signed certificate by default
                        "originRequest": {"noTLSVerify": True},
                    }
                )
            ingress.append({"service": "http_status:404"})
            signature = _signature(ingress)
            if force or signature != state["ingress_signature"]:
                try:
                    api.put_tunnel_ingress(cf["account_id"], cf["tunnel_id"], ingress)
                    state["ingress_signature"] = signature
                except CloudflareError as e:
                    status["error"] = f"Tunnel config: {e}"

        # ---- DNS records
        if cf["dns_enabled"] and not public_ip:
            # Without an IP the A records would look unwanted and get deleted
            return
        desired = self._desired_dns(servers, public_ip)
        signature = _signature(desired)
        if not force and signature == state["dns_signature"]:
            return
        wanted_keys = {(r["name"], r["type"]) for r in desired}
        failed = False
        remaining = []
        for record in state["dns"]:
            if (record["name"], record["type"]) in wanted_keys:
                remaining.append(record)
                continue
            try:
                api.delete_record(cf["zone_id"], record["name"], record["type"])
                logger.info(f"Cloudflare: removed {record['type']} {record['name']}")
            except CloudflareError as e:
                failed = True
                remaining.append(record)
                status["error"] = f"Removing {record['name']}: {e}"
        state["dns"] = remaining

        for record in desired:
            try:
                api.upsert_record(cf["zone_id"], record)
            except CloudflareError as e:
                failed = True
                server_id = self._server_for_host(record["name"])
                message = f"DNS {record['type']} {record['name']}: {e}"
                if server_id:
                    self._server_error(server_id, message)
                else:
                    status["error"] = message
                continue
            key = {"name": record["name"], "type": record["type"]}
            if key not in state["dns"]:
                state["dns"].append(key)
        # Only remember the signature once everything applied, so failures retry
        state["dns_signature"] = "" if failed else signature

    def _server_for_host(self, hostname: str) -> t.Optional[str]:
        for server_id, settings in self.config["servers"].items():
            sub = settings.get("subdomain")
            if not sub:
                continue
            host = f"{sub}.{self.config['cloudflare']['domain']}"
            if hostname == host or hostname.endswith("." + host):
                return server_id
        return None

    # ---------------------------------------------------------------- tunnel
    def setup_tunnel(self) -> None:
        """Looks up the zone and creates (or reuses) the named tunnel."""
        with self._lock:
            cf = self.config["cloudflare"]
            if not cf["api_token"] or not cf["domain"]:
                raise NetworkConfigError("Set the API token and domain first")
            cf["zone_id"] = ""
            api = self._cf_api()
            cf["tunnel_id"] = api.get_or_create_tunnel(
                cf["account_id"], cf["tunnel_name"] or "crafty"
            )
            self.config["state"]["ingress_signature"] = ""
            self.config["state"]["dns_signature"] = ""
            self._save()
        self.request_sync()

    def _ensure_tunnel_process(self) -> None:
        cf = self.config["cloudflare"]
        status = self.status["cloudflare"]
        binary = self.cloudflared.find_binary(cf["cloudflared_path"])
        status["binary"] = binary
        if not (cf["tunnel_enabled"] and cf["tunnel_id"]):
            if self.cloudflared.is_running():
                self.cloudflared.stop()
            return
        if self.cloudflared.is_running():
            return
        if not binary:
            status["error"] = "cloudflared is not installed. Use 'Download cloudflared'."
            return
        try:
            api = self._cf_api()
            token = api.get_tunnel_token(cf["account_id"], cf["tunnel_id"])
            self.cloudflared.start(binary, token)
            Console.info("Cloudflare tunnel started")
        except (CloudflareError, OSError) as e:
            status["error"] = f"Could not start cloudflared: {e}"

    def restart_tunnel(self) -> None:
        self.cloudflared.stop()
        self.request_sync()

    def download_cloudflared(self) -> str:
        path = self.cloudflared.download()
        self.request_sync()
        return path

    # ---------------------------------------------------------------- status
    def get_status(self) -> dict:
        with self._lock:
            status = copy.deepcopy(self.status)
            cf = self.config["cloudflare"]
            servers = {
                str(s["server_id"]): s["server_obj"]
                for s in self.controller.servers.servers_list
            }
            state_upnp = list(self.config["state"]["upnp"])
            state_dns = list(self.config["state"]["dns"])
        status["cloudflare"].update(
            {
                "tunnel_running": self.cloudflared.is_running(),
                "tunnel_connected": self.cloudflared.is_connected(),
                "panel_url": (
                    f"https://{cf['panel_subdomain']}.{cf['domain']}"
                    if cf["tunnel_enabled"] and cf["panel_subdomain"] and cf["domain"]
                    else None
                ),
                "log": list(self.cloudflared.log)[-40:],
            }
        )
        status["upnp_mappings"] = state_upnp
        status["dns_records"] = state_dns
        status["server_list"] = [
            {
                "server_id": server_id,
                "name": srv.name or srv.settings.get("server_name"),
                "type": srv.settings.get("type"),
                "running": bool(srv.check_running()),
                "server_port": srv.settings.get("server_port"),
            }
            for server_id, srv in servers.items()
        ]
        return status


def _parse_extra_ports(value: str) -> t.List[t.Tuple[int, str]]:
    """'24454/udp, 8100' -> [(24454, 'UDP'), (8100, 'TCP')]"""
    ports = []
    for part in str(value or "").replace(";", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        port_str, _, proto = part.partition("/")
        proto = proto or "tcp"
        if not port_str.isdigit() or not 0 < int(port_str) < 65536:
            raise NetworkConfigError(f"Invalid extra port '{part}'")
        if proto not in PROTOCOL_OVERRIDES:
            raise NetworkConfigError(f"Invalid protocol in '{part}'")
        ports += [(int(port_str), p) for p in PROTOCOL_OVERRIDES[proto]]
    return ports


def _signature(data: t.Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
