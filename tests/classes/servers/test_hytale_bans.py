"""Tests for reading Hytale's bans.json."""

import json

import pytest

from app.classes.servers import hytale

BAN = {
    "target": "uuid-1",
    "by": "00000000-0000-0000-0000-000000000000",
    "timestamp": 0,
    "reason": "griefing",
}


@pytest.mark.parametrize(
    "content",
    [[BAN], {"Version": 1, "Bans": [BAN]}],
    ids=["bare-list", "versioned-wrapper"],
)
def test_bans_are_read_from_either_file_format(tmp_path, content):
    """Both the bare list and the versioned wrapper yield the ban."""
    (tmp_path / "bans.json").write_text(json.dumps(content), encoding="utf-8")

    banned = hytale.get_banned_players(str(tmp_path))

    assert [ban["uuid"] for ban in banned] == ["uuid-1"]
    assert banned[0]["source"] == "Server"
    assert banned[0]["reason"] == "griefing"


def test_an_empty_versioned_file_has_no_bans(tmp_path):
    """The file a fresh server writes reads as an empty list."""
    (tmp_path / "bans.json").write_text(
        json.dumps({"Version": 1, "Bans": []}), encoding="utf-8"
    )

    assert not hytale.get_banned_players(str(tmp_path))


def test_current_format_entries_are_read(tmp_path):
    """Capitalized keys and an ISO timestamp fill in every field."""
    content = {
        "Version": 1,
        "Bans": [
            {
                "Target": "uuid-1",
                "By": "uuid-2",
                "Timestamp": "2026-10-13T19:02:30.185934148Z",
                "Reason": "Test",
            }
        ],
    }
    (tmp_path / "bans.json").write_text(json.dumps(content), encoding="utf-8")
    players = tmp_path / "universe" / "players"
    players.mkdir(parents=True)
    (players / "uuid-2.json").write_text(
        json.dumps({"Components": {"Nameplate": {"Text": "Admin"}}}),
        encoding="utf-8",
    )

    [ban] = hytale.get_banned_players(str(tmp_path))

    assert ban["uuid"] == ban["unban_target"] == "uuid-1"
    assert ban["source"] == "Admin"
    assert ban["reason"] == "Test"
    assert ban["created"] == "2026-10-13 19:02:30 +0000"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (1_000, "1970-01-01 00:00:01 +0000"),
        ("2026-10-13T19:02:30Z", "2026-10-13 19:02:30 +0000"),
        ("not a date", "1970-01-01 00:00:00 +0000"),
    ],
    ids=["epoch-millis", "iso-without-fraction", "unparseable"],
)
def test_ban_timestamps_are_normalized(timestamp, expected):
    """Every timestamp form yields the string the panel handler parses."""
    # pylint: disable-next=protected-access
    parsed = hytale._parse_ban_timestamp(timestamp)

    assert parsed.strftime("%Y-%m-%d %H:%M:%S %z") == expected
