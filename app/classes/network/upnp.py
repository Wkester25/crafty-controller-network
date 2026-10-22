"""Minimal UPnP IGD client (SSDP discovery + SOAP port mapping).

Implemented on top of the standard library and ``requests`` so no compiled
dependency (miniupnpc) is needed on Windows.
"""

import ipaddress
import logging
import socket
import time
import typing as t
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import requests

logger = logging.getLogger(__name__)

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = [
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
]
WAN_SERVICES = [
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
]
# UPnP error code returned by routers that refuse non-zero lease durations
ONLY_PERMANENT_LEASES = "725"
CONFLICT_IN_MAPPING = "718"
NO_SUCH_ENTRY = "714"


class UpnpError(Exception):
    def __init__(self, message: str, code: t.Optional[str] = None):
        super().__init__(message)
        self.code = code


def is_private_ip(ip: str) -> bool:
    """True for RFC1918, CGNAT (100.64/10), loopback and link-local addresses."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr in ipaddress.ip_network("100.64.0.0/10")
    )


class UpnpGateway:
    def __init__(self, location: str, control_url: str, service_type: str):
        self.location = location
        self.control_url = control_url
        self.service_type = service_type
        self.host = urlparse(location).hostname
        self.local_ip = self._detect_local_ip()

    def __repr__(self):
        return f"UpnpGateway({self.control_url}, {self.service_type})"

    def _detect_local_ip(self) -> str:
        # Connecting a UDP socket sends nothing, but makes the OS pick the
        # interface that routes to the gateway - which is the IP to forward to.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((self.host, 1900))
            return sock.getsockname()[0]
        finally:
            sock.close()

    # ------------------------------------------------------------------ SOAP
    def _soap(self, action: str, args: t.Dict[str, t.Any]) -> ElementTree.Element:
        arg_xml = "".join(
            f"<{key}>{escape(str(value))}</{key}>" for key, value in args.items()
        )
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{self.service_type}">{arg_xml}'
            f"</u:{action}></s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{self.service_type}#{action}"',
        }
        try:
            resp = requests.post(
                self.control_url, data=body.encode(), headers=headers, timeout=6
            )
        except requests.RequestException as e:
            raise UpnpError(f"{action} request failed: {e}") from e

        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError as e:
            raise UpnpError(f"{action} returned invalid XML ({resp.status_code})") from e

        if resp.status_code != 200:
            code = _find_text(root, "errorCode")
            desc = _find_text(root, "errorDescription") or f"HTTP {resp.status_code}"
            raise UpnpError(f"{action} failed: {desc} (code {code})", code)
        return root

    # --------------------------------------------------------------- actions
    def get_external_ip(self) -> str:
        root = self._soap("GetExternalIPAddress", {})
        ip = _find_text(root, "NewExternalIPAddress")
        if not ip:
            raise UpnpError("Router did not report an external IP")
        return ip

    def get_mapping(self, external_port: int, protocol: str) -> t.Optional[dict]:
        try:
            root = self._soap(
                "GetSpecificPortMappingEntry",
                {
                    "NewRemoteHost": "",
                    "NewExternalPort": external_port,
                    "NewProtocol": protocol,
                },
            )
        except UpnpError as e:
            if e.code == NO_SUCH_ENTRY or e.code is None:
                return None
            raise
        return {
            "internal_client": _find_text(root, "NewInternalClient"),
            "internal_port": _find_text(root, "NewInternalPort"),
            "description": _find_text(root, "NewPortMappingDescription"),
        }

    def add_mapping(
        self,
        external_port: int,
        protocol: str,
        internal_port: int,
        description: str,
        lease_seconds: int = 3600,
    ) -> int:
        """Adds a mapping. Returns the lease actually used (0 = permanent)."""
        args = {
            "NewRemoteHost": "",
            "NewExternalPort": external_port,
            "NewProtocol": protocol,
            "NewInternalPort": internal_port,
            "NewInternalClient": self.local_ip,
            "NewEnabled": 1,
            "NewPortMappingDescription": description,
            "NewLeaseDuration": lease_seconds,
        }
        try:
            self._soap("AddPortMapping", args)
            return lease_seconds
        except UpnpError as e:
            if e.code == ONLY_PERMANENT_LEASES and lease_seconds:
                args["NewLeaseDuration"] = 0
                self._soap("AddPortMapping", args)
                return 0
            if e.code == CONFLICT_IN_MAPPING:
                raise UpnpError(
                    f"Port {external_port}/{protocol} is already forwarded to "
                    "another device on your network",
                    e.code,
                ) from e
            raise

    def delete_mapping(self, external_port: int, protocol: str) -> None:
        try:
            self._soap(
                "DeletePortMapping",
                {
                    "NewRemoteHost": "",
                    "NewExternalPort": external_port,
                    "NewProtocol": protocol,
                },
            )
        except UpnpError as e:
            if e.code != NO_SUCH_ENTRY:
                raise


def _find_text(root: ElementTree.Element, tag: str) -> t.Optional[str]:
    # Responses are namespaced inconsistently between routers; match local name
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return (el.text or "").strip()
    return None


def _ssdp_locations(timeout: float) -> t.List[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    locations: t.List[str] = []
    try:
        for target in SEARCH_TARGETS:
            msg = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {target}\r\n\r\n"
            )
            sock.sendto(msg.encode(), SSDP_ADDR)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            for line in data.decode(errors="ignore").split("\r\n"):
                if line.lower().startswith("location:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc not in locations:
                        locations.append(loc)
    finally:
        sock.close()
    return locations


def _gateway_from_location(location: str) -> t.Optional[UpnpGateway]:
    try:
        resp = requests.get(location, timeout=5)
        root = ElementTree.fromstring(resp.content)
    except (requests.RequestException, ElementTree.ParseError) as e:
        logger.debug(f"UPnP: could not read device description {location}: {e}")
        return None

    base = _find_text(root, "URLBase") or location
    for service in root.iter():
        if service.tag.rsplit("}", 1)[-1] != "service":
            continue
        service_type = _find_text(service, "serviceType")
        if service_type in WAN_SERVICES:
            control = _find_text(service, "controlURL")
            if control:
                return UpnpGateway(location, urljoin(base, control), service_type)
    return None


def discover(timeout: float = 3.0) -> UpnpGateway:
    """Finds the first Internet Gateway Device on the LAN."""
    locations = _ssdp_locations(timeout)
    if not locations:
        raise UpnpError(
            "No UPnP router found. Make sure UPnP is enabled in your router settings."
        )
    for location in locations:
        gateway = _gateway_from_location(location)
        if gateway:
            logger.info(f"UPnP: using gateway {gateway}")
            return gateway
    raise UpnpError(
        "Found UPnP devices, but none of them offer port forwarding (WANIPConnection)."
    )
