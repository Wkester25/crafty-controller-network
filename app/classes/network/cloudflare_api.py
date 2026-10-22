"""Thin wrapper over the Cloudflare v4 REST API for tunnels and DNS records.

Required API token permissions:
    Account > Cloudflare Tunnel > Edit
    Zone    > DNS               > Edit
    Zone    > Zone              > Read
"""

import logging
import typing as t

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"
# Tag written into the comment of every DNS record Crafty creates, so it never
# touches records it does not own.
MANAGED_COMMENT = "managed-by-crafty"


class CloudflareError(Exception):
    pass


class CloudflareApi:
    def __init__(self, api_token: str):
        if not api_token:
            raise CloudflareError("No Cloudflare API token configured")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> t.Any:
        try:
            resp = self.session.request(
                method, f"{API_BASE}{path}", timeout=15, **kwargs
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise CloudflareError(f"Cloudflare API request failed: {e}") from e

        if not data.get("success"):
            errors = "; ".join(
                f"{err.get('code')}: {err.get('message')}"
                for err in data.get("errors", [])
            )
            raise CloudflareError(errors or f"HTTP {resp.status_code}")
        return data.get("result")

    # ----------------------------------------------------------------- zones
    def find_zone(self, domain: str) -> t.Tuple[str, str]:
        """Returns (zone_id, account_id) for the domain."""
        zones = self._request("GET", "/zones", params={"name": domain})
        if not zones:
            raise CloudflareError(
                f"Zone '{domain}' not found. Check the domain is on this Cloudflare "
                "account and the token has Zone:Read."
            )
        return zones[0]["id"], zones[0]["account"]["id"]

    # --------------------------------------------------------------- tunnels
    def get_or_create_tunnel(self, account_id: str, name: str) -> str:
        tunnels = self._request(
            "GET",
            f"/accounts/{account_id}/cfd_tunnel",
            params={"name": name, "is_deleted": "false"},
        )
        if tunnels:
            return tunnels[0]["id"]
        tunnel = self._request(
            "POST",
            f"/accounts/{account_id}/cfd_tunnel",
            json={"name": name, "config_src": "cloudflare"},
        )
        logger.info(f"Cloudflare: created tunnel {name} ({tunnel['id']})")
        return tunnel["id"]

    def get_tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        return self._request(
            "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token"
        )

    def put_tunnel_ingress(
        self, account_id: str, tunnel_id: str, ingress: t.List[dict]
    ) -> None:
        self._request(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            json={"config": {"ingress": ingress}},
        )

    # ------------------------------------------------------------------- DNS
    def list_records(self, zone_id: str, name: str, rtype: str) -> t.List[dict]:
        return self._request(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"name": name, "type": rtype},
        )

    def upsert_record(self, zone_id: str, record: dict) -> dict:
        """Creates or updates the record with the same name + type.

        Refuses to overwrite a record that was not created by Crafty.
        """
        record = {**record, "comment": MANAGED_COMMENT}
        existing = self.list_records(zone_id, record["name"], record["type"])
        if existing:
            current = existing[0]
            if current.get("comment") != MANAGED_COMMENT:
                raise CloudflareError(
                    f"A {record['type']} record for {record['name']} already exists "
                    "and was not created by Crafty. Delete it in Cloudflare first."
                )
            if _record_matches(current, record):
                return current
            return self._request(
                "PUT", f"/zones/{zone_id}/dns_records/{current['id']}", json=record
            )
        return self._request("POST", f"/zones/{zone_id}/dns_records", json=record)

    def delete_record(self, zone_id: str, name: str, rtype: str) -> None:
        for record in self.list_records(zone_id, name, rtype):
            if record.get("comment") == MANAGED_COMMENT:
                self._request("DELETE", f"/zones/{zone_id}/dns_records/{record['id']}")


def _record_matches(current: dict, wanted: dict) -> bool:
    if current.get("proxied", False) != wanted.get("proxied", False):
        return False
    if "data" in wanted:
        cur = current.get("data") or {}
        return all(cur.get(k) == v for k, v in wanted["data"].items())
    return current.get("content") == wanted.get("content")


def get_public_ip() -> str:
    """Public IPv4 as seen by Cloudflare's edge."""
    try:
        resp = requests.get("https://1.1.1.1/cdn-cgi/trace", timeout=8)
        for line in resp.text.splitlines():
            if line.startswith("ip="):
                return line[3:].strip()
    except requests.RequestException as e:
        raise CloudflareError(f"Could not determine public IP: {e}") from e
    raise CloudflareError("Could not determine public IP")
