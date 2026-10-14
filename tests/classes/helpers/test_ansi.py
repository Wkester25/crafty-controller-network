"""Tests for the ANSI -> HTML console renderer."""

import pytest

from app.classes.helpers import ansi


def test_plain_line_is_untouched() -> None:
    line = "[16:58:26 INFO]: Loaded 7 recipes"
    assert ansi.has_ansi(line) is False
    assert ansi.ansi_to_html(line) == line


def test_basic_foreground_color() -> None:
    assert ansi.ansi_to_html("\x1b[31mboom\x1b[0m") == (
        '<span class="ansi-fg-1">boom</span>'
    )


def test_bright_foreground_color() -> None:
    assert ansi.ansi_to_html("\x1b[92mok\x1b[m") == '<span class="ansi-fg-10">ok</span>'


def test_background_color() -> None:
    assert ansi.ansi_to_html("\x1b[41mred\x1b[0m") == (
        '<span class="ansi-bg-1">red</span>'
    )


def test_attributes_combine_with_color() -> None:
    assert ansi.ansi_to_html("\x1b[1;4;36mhi\x1b[0m") == (
        '<span class="ansi-fg-6 ansi-bold ansi-underline">hi</span>'
    )


def test_attribute_reset_codes() -> None:
    assert ansi.ansi_to_html("\x1b[1mbold\x1b[22mplain") == (
        '<span class="ansi-bold">bold</span>plain'
    )


def test_minecraft_bold_uses_sgr_21() -> None:
    """Java servers emit SGR 21 for Minecraft's bold code."""
    assert ansi.ansi_to_html("\x1b[0;34;1m\x1b[21mBOLD") == (
        '<span class="ansi-fg-4 ansi-bold">BOLD</span>'
    )


def test_empty_params_mean_reset() -> None:
    assert ansi.ansi_to_html("\x1b[32mgreen\x1b[mplain") == (
        '<span class="ansi-fg-2">green</span>plain'
    )


def test_truecolor_uses_inline_style() -> None:
    assert ansi.ansi_to_html("\x1b[38;2;85;255;255mrgb\x1b[0m") == (
        '<span style="color:#55ffff">rgb</span>'
    )


def test_truecolor_background_uses_inline_style() -> None:
    assert ansi.ansi_to_html("\x1b[48;2;0;0;128mbg\x1b[0m") == (
        '<span style="background-color:#000080">bg</span>'
    )


def test_256_color_low_slots_reuse_the_palette_classes() -> None:
    assert (
        ansi.ansi_to_html("\x1b[38;5;9mx\x1b[0m") == '<span class="ansi-fg-9">x</span>'
    )


def test_256_color_cube() -> None:
    # 196 is the pure red corner of the 6x6x6 cube.
    assert ansi.ansi_to_html("\x1b[38;5;196mx\x1b[0m") == (
        '<span style="color:#ff0000">x</span>'
    )


def test_256_color_grayscale_ramp() -> None:
    assert ansi.ansi_to_html("\x1b[38;5;232mx\x1b[0m") == (
        '<span style="color:#080808">x</span>'
    )


def test_color_state_persists_across_segments() -> None:
    assert ansi.ansi_to_html("\x1b[33mone\x1b[1mtwo\x1b[0mthree") == (
        '<span class="ansi-fg-3">one</span>'
        '<span class="ansi-fg-3 ansi-bold">two</span>'
        "three"
    )


def test_unterminated_color_does_not_leak_past_the_line() -> None:
    # No trailing reset: the span must still close, or the next line would
    # inherit the color.
    out = ansi.ansi_to_html("\x1b[31mnever reset")
    assert out == '<span class="ansi-fg-1">never reset</span>'
    assert out.count("<span") == out.count("</span>")


def test_text_after_the_last_escape_is_kept() -> None:
    assert ansi.ansi_to_html("\x1b[31mred\x1b[0m tail text").endswith(" tail text")


def test_html_is_escaped() -> None:
    assert ansi.ansi_to_html("\x1b[31m<script>x</script>\x1b[0m") == (
        '<span class="ansi-fg-1">&lt;script&gt;x&lt;/script&gt;</span>'
    )


def test_html_is_escaped_without_any_color() -> None:
    assert ansi.ansi_to_html("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_reverse_video_swaps_channels() -> None:
    assert ansi.ansi_to_html("\x1b[31;7mrev\x1b[0m") == (
        '<span class="ansi-bg-1" style="color:var(--term-bg, #1c1e2f)">rev</span>'
    )


def test_non_sgr_escapes_are_dropped() -> None:
    # Cursor-home plus an erase-display, as some launchers emit on start.
    assert ansi.ansi_to_html("\x1b[H\x1b[2Jcleared") == "cleared"


def test_control_characters_are_stripped() -> None:
    assert ansi.ansi_to_html("\x1b[32ma\x07b\rc\x1b[0m") == (
        '<span class="ansi-fg-2">abc</span>'
    )


def test_malformed_sequence_does_not_raise() -> None:
    assert "broken" in ansi.ansi_to_html("\x1b[38;5mbroken\x1b[0m")


def test_text_filter_only_sees_server_text() -> None:
    seen = []

    def spy(chunk):
        seen.append(chunk)
        return chunk.upper()

    out = ansi.ansi_to_html("\x1b[31mred\x1b[0mplain", text_filter=spy)
    assert seen == ["red", "plain"]
    assert out == '<span class="ansi-fg-1">RED</span>PLAIN'


def test_strip_ansi_removes_everything() -> None:
    assert ansi.strip_ansi("\x1b[1;31m[ERROR]\x1b[0m boom\x07") == "[ERROR] boom"


@pytest.mark.parametrize(
    "line",
    [
        "\x1b[0;32m[16:58:29 INFO]: \x1b[0;36mLuckPerms v5.4.79\x1b[m",
        "\x1b[31m\x1b[1mWARNING\x1b[0m",
        "no color at all",
        "",
    ],
)
def test_output_is_balanced(line: str) -> None:
    out = ansi.ansi_to_html(line)
    assert out.count("<span") == out.count("</span>")
    assert "\x1b" not in out
