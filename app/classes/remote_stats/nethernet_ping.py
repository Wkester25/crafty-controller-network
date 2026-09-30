import http.client
import json
import logging

logger = logging.getLogger(__name__)


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
        conn.request("GET", "/v1/join")
        response = conn.getresponse()
        if response.status != 200:
            logger.debug(
                "NetherNet status request to %s:%s returned HTTP %s",
                ip,
                port,
                response.status,
            )
            return None
        data = json.loads(response.read())
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
