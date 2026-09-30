import http.client
import json
import logging
import os
import socket
import struct

logger = logging.getLogger(__name__)

# struct linger is two u_shorts on Windows and two ints elsewhere.
_LINGER_ABORT = struct.pack("HH" if os.name == "nt" else "ii", 1, 0)


def ping_nethernet(ip, port, timeout: float = 3):
    """Query a NetherNet Bedrock server's status over plain HTTP.

    Bedrock Dedicated Server 1.26.51+ only accepts the NetherNet transport and no
    longer answers RakNet unconnected pings. Instead it serves
    GET /v1/join on its TCP server-port, returning JSON such as
    {"name": ..., "protocol": 2193, "version": "1.26.52", "level": ...,
     "players": 0, "maxPlayers": 10, "gameType": 1}.

    Returns a dict with the same keys RaknetPing produces, so
    Stats.parse_server_raknet_ping() can consume either, or None if the server
    does not speak this endpoint (e.g. an older RakNet-only server).
    """
    conn = http.client.HTTPConnection(ip, int(port), timeout=timeout)
    try:
        conn.connect()
        # BDS closes this connection first, which would leave TIME_WAIT on its
        # own port, and it binds that port without SO_REUSEADDR: on Linux a
        # restart within ~60 s of a poll then silently fails to listen. Reset
        # the connection on close instead so no TIME_WAIT is left behind.
        conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _LINGER_ABORT)
        conn.request("GET", "/v1/join")
        with conn.getresponse() as response:
            status = response.status
            body = response.read()
        if status != 200:
            logger.debug(
                "NetherNet status request to %s:%s returned HTTP %s", ip, port, status
            )
            return None
        data = json.loads(body)
        return {
            "server_motd": data["name"],
            "server_version_name": data["version"],
            "server_protocol_version": data.get("protocol"),
            "server_player_count": data["players"],
            "server_player_max": data["maxPlayers"],
            "server_level_name": data.get("level"),
            "server_game_mode_num": data.get("gameType"),
        }
    except (OSError, http.client.HTTPException) as e:
        logger.debug("No NetherNet status from %s:%s: %s", ip, port, e)
    except (ValueError, KeyError, TypeError) as e:
        logger.debug("Unexpected NetherNet status from %s:%s: %s", ip, port, e)
    finally:
        conn.close()
    return None
