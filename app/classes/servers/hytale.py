"""Hytale-specific server logic.

Isolated in its own module so that breaking changes to the Hytale on-disk
formats (``bans.json`` and the ``universe/players/<uuid>.json`` profiles) have a
small, obvious blast radius instead of being entangled with the generic server
controller.
"""

import os
import re
import json
import logging
import datetime

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)


def get_banned_players(server_path):
    """Read a Hytale ``bans.json`` and normalise it to the Minecraft shape.

    Hytale records bans by UUID only, so the banned player's name is resolved
    from its ``universe/players/<uuid>.json`` file (falling back to the raw
    UUID), and the ``timestamp`` is converted to the same datetime string the
    Minecraft path produces. ``unban_target`` carries the UUID, which the
    ``unban`` command accepts even when the name cannot be resolved. Returns
    ``{}`` if the file cannot be read.
    """
    path = os.path.join(server_path, "bans.json")

    try:
        with open(Helpers.get_os_understandable_path(path), encoding="utf-8") as file:
            content = file.read()
            file.close()
    except Exception as ex:
        logger.exception(ex)
        return {}

    bans = json.loads(content)
    # Newer servers wrap the list as {"Version": 1, "Bans": [...]}.
    if isinstance(bans, dict):
        bans = bans.get("Bans", [])

    banned_players = []
    for ban in bans:
        # Newer servers capitalize the keys ("Target", "By", ...).
        ban = {key.lower(): value for key, value in ban.items()}
        target = ban.get("target", "")
        source = ban.get("by", "")
        # The all-zero UUID denotes a ban issued by the server/console.
        if not source.strip("0-"):
            source = "Server"
        else:
            source = resolve_player_name(server_path, source)
        banned_players.append(
            {
                "uuid": target,
                "name": resolve_player_name(server_path, target),
                "unban_target": target,
                "source": source,
                "reason": ban.get("reason") or "None",
                "created": _parse_ban_timestamp(ban.get("timestamp", 0)).strftime(
                    "%Y-%m-%d %H:%M:%S %z"
                ),
            }
        )

    return banned_players


def _parse_ban_timestamp(timestamp):
    """Convert a ban timestamp to an aware UTC datetime.

    Older servers write epoch milliseconds; newer ones write an ISO 8601 string
    with nanosecond precision. Unparseable values fall back to the epoch.
    """
    epoch = datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
    if isinstance(timestamp, (int, float)):
        return epoch + datetime.timedelta(milliseconds=timestamp)

    # fromisoformat before Python 3.11 rejects "Z" and more than 6 digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(timestamp)).replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return epoch
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def resolve_player_name(server_path, player_uuid):
    """Resolve a Hytale player UUID to its name via its universe profile.

    Returns the raw UUID when the profile is missing or unreadable so the caller
    always has a usable identifier to display.
    """
    if not player_uuid:
        return player_uuid

    path = os.path.join(server_path, "universe", "players", f"{player_uuid}.json")
    try:
        with open(Helpers.get_os_understandable_path(path), encoding="utf-8") as file:
            components = json.loads(file.read()).get("Components", {})
    except Exception:
        return player_uuid

    nameplate = components.get("Nameplate", {}).get("Text")
    display_name = (
        components.get("DisplayName", {}).get("DisplayName", {}).get("RawText")
    )
    return nameplate or display_name or player_uuid
