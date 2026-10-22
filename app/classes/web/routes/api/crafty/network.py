import logging

import orjson
from jsonschema import ValidationError, validate
from tornado.ioloop import IOLoop

from app.classes.network.cloudflare_api import CloudflareError
from app.classes.network.network_manager import NetworkConfigError
from app.classes.network.upnp import UpnpError
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

network_patch_schema = {
    "type": "object",
    "properties": {
        "upnp": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "only_while_running": {"type": "boolean"},
                "lease_seconds": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "cloudflare": {
            "type": "object",
            "properties": {
                "api_token": {"type": "string"},
                "domain": {"type": "string"},
                "tunnel_name": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                "tunnel_enabled": {"type": "boolean"},
                "panel_subdomain": {"type": "string"},
                "dns_enabled": {"type": "boolean"},
                "cloudflared_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "servers": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "subdomain": {"type": "string"},
                    "upnp": {"type": "boolean"},
                    "dns": {"type": "boolean"},
                    "protocol": {"enum": ["auto", "tcp", "udp", "both"]},
                    "port": {"type": "integer", "minimum": 0, "maximum": 65535},
                    "extra_ports": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

ACTIONS = {
    "sync": lambda net: net.sync(force=True),
    "setup_tunnel": lambda net: net.setup_tunnel(),
    "restart_tunnel": lambda net: net.restart_tunnel(),
    "download_cloudflared": lambda net: net.download_cloudflared(),
}


class _NetworkBase(BaseApiHandler):
    def _superuser_auth(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return None
        if not auth_data[4]["superuser"]:
            self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
            return None
        return auth_data

    def _ok(self):
        net = self.controller.network
        self.finish_json(
            200,
            {
                "status": "ok",
                "data": {"config": net.public_config(), "status": net.get_status()},
            },
        )


class ApiCraftyNetworkHandler(_NetworkBase):
    def get(self):
        if not self._superuser_auth():
            return
        self._ok()

    def patch(self):
        auth_data = self._superuser_auth()
        if not auth_data:
            return
        try:
            data = orjson.loads(self.request.body)
            validate(data, network_patch_schema)
        except orjson.JSONDecodeError as why:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(why)}
            )
        except ValidationError as why:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": why.message,
                },
            )

        try:
            self.controller.network.update_config(data)
        except NetworkConfigError as why:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_SETTING", "error_data": str(why)}
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            "updated network settings (UPnP / Cloudflare)",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )
        self._ok()


class ApiCraftyNetworkActionHandler(_NetworkBase):
    async def post(self, action: str):
        auth_data = self._superuser_auth()
        if not auth_data:
            return
        func = ACTIONS.get(action)
        if not func:
            return self.finish_json(
                404, {"status": "error", "error": "UNKNOWN_ACTION", "error_data": action}
            )
        try:
            # These talk to the router / Cloudflare / GitHub; keep the IOLoop free
            await IOLoop.current().run_in_executor(
                None, func, self.controller.network
            )
        except (NetworkConfigError, CloudflareError, UpnpError, OSError) as why:
            return self.finish_json(
                400, {"status": "error", "error": "NETWORK_ERROR", "error_data": str(why)}
            )
        except Exception as why:  # pylint: disable=broad-except
            logger.exception(f"Network action {action} failed")
            return self.finish_json(
                500, {"status": "error", "error": "NETWORK_ERROR", "error_data": str(why)}
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"ran network action {action}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )
        self._ok()
