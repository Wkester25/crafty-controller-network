import struct
import socket
import base64
import json
import os
import re
import logging.config
import uuid
import random

from app.classes.remote_stats.nethernet_ping import ping_nethernet
from app.classes.remote_stats.raknet_ping import RaknetPing

from app.classes.shared.console import Console

logger = logging.getLogger(__name__)
MOTD_CODES = ["bold", "italic", "underlined", "strikethrough"]


class Server:
    def __init__(self, data):
        if isinstance(data, str):
            logger.error(
                "Failed to calculate stats. Expected object. "
                f"Server returned string: {data}"
            )
            return
        self.description = data.get("description")
        # print(self.description)
        if isinstance(self.description, dict):
            # cat server
            if "translate" in self.description:
                self.description = self.description["translate"]

            # waterfall / bungee
            elif "extra" in self.description:
                lines = []

                description = self.description
                if "text" in description.keys():
                    lines.append(description["text"])
                if "extra" in description.keys():
                    if isinstance(description["extra"], list):
                        for e in description["extra"]:
                            if not isinstance(e, dict):
                                lines.append(e)
                                continue
                            # Conversion format code needed only for Java Version
                            lines.append(get_code_format("reset"))
                            for item in MOTD_CODES:
                                if e.get(item, False):
                                    lines.append(get_code_format(item))
                            if "color" in e.keys():
                                lines.append(get_code_format(e["color"]))
                            # Then append the text
                            if "text" in e.keys():
                                if e["text"] == "\n":
                                    lines.append("§§")
                                else:
                                    lines.append(e["text"])

                total_text = " ".join(lines)
                self.description = total_text

            # normal MC
            else:
                self.description = self.description["text"]

        self.icon = base64.b64decode(data.get("favicon", "")[22:])
        try:
            self.players = Players(data["players"]).report()
        except KeyError:
            logger.error("Error geting player information key error")
            self.players = []
        self.version = data["version"]["name"]
        self.protocol = data["version"]["protocol"]


class Players(list):
    def __init__(self, data):
        super().__init__(Player(x) for x in data.get("sample", []))
        self.max = data.get("max", 0)
        self.online = data.get("online", 0)

    def report(self):
        players = []

        for player in self:
            players.append(str(player))

        r_data = {"online": self.online, "max": self.max, "players": players}

        return json.dumps(r_data)


class Player:
    def __init__(self, data):
        self.id = data.get("id", "")
        self.name = data.get("name", "Anonymous")

    def __str__(self):
        return self.name


def get_code_format(format_name):
    root_dir = os.path.abspath(os.path.curdir)
    format_file = os.path.join(root_dir, "app", "config", "motd_format.json")
    try:
        with open(format_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if format_name in data.keys():
            return data.get(format_name)
        logger.error(f"Format MOTD Error: format name {format_name} does not exist")
        Console.error(f"Format MOTD Error: format name {format_name} does not exist")
        return ""

    except Exception as e:
        logger.critical(f"Config File Error: Unable to read {format_file} due to {e}")
        Console.critical(f"Config File Error: Unable to read {format_file} due to {e}")

    return ""


# For the rest of requests see wiki.vg/Protocol
def ping(ip, port):
    def read_var_int():
        i = 0
        j = 0
        while True:
            try:
                k = sock.recv(1)
                if not k:
                    raise ValueError()
            except:
                return 0
            k = k[0]
            i |= (k & 0x7F) << (j * 7)
            j += 1
            if j > 5:
                raise ValueError("var_int too big")
            if not k & 0x80:
                return i

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((ip, port))

    except:
        return False

    try:
        host = ip.encode("utf-8")
        data = b""  # wiki.vg/Server_List_Ping
        data += b"\x00"  # packet ID
        data += b"\x04"  # protocol variant
        data += struct.pack(">b", len(host)) + host
        data += struct.pack(">H", port)
        data += b"\x01"  # next state
        data = struct.pack(">b", len(data)) + data
        sock.sendall(data + b"\x01\x00")  # handshake + status ping
        length = read_var_int()  # full packet length
        if length < 10:
            return length >= 0

        sock.recv(1)  # packet type, 0 for pings
        length = read_var_int()  # string length
        data = b""
        while len(data) != length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                return False

            data += chunk
        logger.debug(f"Server reports this data on ping: {data}")
        try:
            return Server(json.loads(data))
        except (KeyError, json.decoder.JSONDecodeError):
            return {}
    finally:
        sock.close()


# For the rest of requests see wiki.vg/Protocol
def ping_raknet(ip, port):
    rand = random.Random()
    try:
        # pylint: disable=consider-using-f-string
        rand.seed("".join(re.findall("..", "%012x" % uuid.getnode())))
        client_guid = uuid.UUID(int=rand.getrandbits(32)).int
    except:
        client_guid = 0
    try:
        brp = RaknetPing(ip, port, client_guid)
        return brp.ping()
    except OSError as e:
        # Expected while a server is stopped or still starting, e.g. Windows
        # reports the ICMP port-unreachable as ConnectionResetError.
        logger.warning("Unable to get RakNet stats from %s:%s: %r", ip, port, e)
    except:
        logger.exception("Unable to get RakNet stats")


def ping_bedrock(ip, port):
    # BDS 1.26.51+ only speaks NetherNet, which answers status over HTTP on the
    # TCP server-port. Try that first: it is fast, and a RakNet-only server
    # refuses the TCP connection immediately, so we fall straight through.
    return ping_nethernet(ip, port) or ping_raknet(ip, port)
