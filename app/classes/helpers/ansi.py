"""Render ANSI SGR escape sequences from server stdout as terminal HTML.

This runs on every line every running server prints, so style states and the
tags they render to are memoized - servers repeat the same few sequences.
"""

import html
import re

# Non-SGR escapes (cursor movement, titles, ...) carry no color, so drop them.
# The final byte class excludes "m" so this never eats an SGR sequence.
NON_SGR_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-ln-~])")

# One capture group, so splitting yields text, params, text, params, ...
SGR_SPLIT_RE = re.compile(r"\x1b\[([0-9;]*)m")

# Non-printable junk that would only confuse the HTML. Tabs and newlines
# survive; carriage returns and backspaces do not.
CONTROL_JUNK_RE = re.compile(r"[\x00-\x08\x0b-\x0d\x0e-\x1f\x7f]")

# fg, bg, bold, dim, italic, underline, strike, reverse.
# fg/bg are None (terminal default), an int 0-15 (palette slot) or "#rrggbb".
DEFAULT_STATE = (None, None, False, False, False, False, False, False)


# Bounded so a server spamming unique true-color values cannot grow them
# without limit.
_MAX_CACHE = 4096
_TAG_CACHE = {}
_TRANSITION_CACHE = {}


def _build_256_palette():
    """Build the xterm 256-color palette."""
    palette = [None] * 256

    # 0-15 stay as palette slots so they follow the theme.
    for i in range(16):
        palette[i] = i

    # 16-231: 6x6x6 color cube.
    levels = (0, 95, 135, 175, 215, 255)
    for i in range(216):
        red = levels[(i // 36) % 6]
        green = levels[(i // 6) % 6]
        blue = levels[i % 6]
        palette[16 + i] = f"#{red:02x}{green:02x}{blue:02x}"

    # 232-255: 24 step grayscale ramp.
    for i in range(24):
        level = 8 + i * 10
        palette[232 + i] = f"#{level:02x}{level:02x}{level:02x}"

    return tuple(palette)


PALETTE_256 = _build_256_palette()


def _extended_color(params, index):
    """Read a 38/48 extended color argument.

    :param params: The full list of integer SGR parameters.
    :param index: Position of the 38 or 48 selector within ``params``.
    :return: ``(color, next_index)`` where color is a palette slot, a hex
        string or ``None``, and next_index is the first unconsumed parameter.
    """
    # Match the whole tail so each form can state its argument count.
    match params[index:]:
        case [_, 5, int() as slot, *_] if 0 <= slot <= 255:
            return PALETTE_256[slot], index + 3
        case [_, 5, *_]:
            return None, index + 3
        case [_, 2, int() as red, int() as green, int() as blue, *_] if (
            0 <= red <= 255 and 0 <= green <= 255 and 0 <= blue <= 255
        ):
            return f"#{red:02x}{green:02x}{blue:02x}", index + 5
        case [_, 2, _, _, _, *_]:
            return None, index + 5
        case [_]:
            return None, index + 1
        case _:
            return None, index + 2


def _apply_params(state, raw_params):
    """Fold an SGR parameter string into the current style state."""
    # "\x1b[m" is shorthand for "\x1b[0m".
    if not raw_params:
        return DEFAULT_STATE

    try:
        params = [int(part) if part else 0 for part in raw_params.split(";")]
    except ValueError:
        return state

    fg, bg, bold, dim, italic, underline, strike, reverse = state

    i = 0
    while i < len(params):
        code = params[i]
        i += 1

        match code:
            case 0:
                fg = bg = None
                bold = dim = italic = underline = strike = reverse = False
            case 1:
                bold = True
            case 2:
                dim = True
            case 3:
                italic = True
            case 4:
                underline = True
            case 7:
                reverse = True
            case 9:
                strike = True
            case 21:
                # The spec says double underline or bold off, but
                # TerminalConsoleAppender emits 21 for Minecraft's bold code.
                bold = True
            case 22:
                bold = dim = False
            case 23:
                italic = False
            case 24:
                underline = False
            case 27:
                reverse = False
            case 29:
                strike = False
            case 38:
                fg, i = _extended_color(params, i - 1)
            case 39:
                fg = None
            case 48:
                bg, i = _extended_color(params, i - 1)
            case 49:
                bg = None
            case _ if 30 <= code <= 37:
                fg = code - 30
            case _ if 40 <= code <= 47:
                bg = code - 40
            case _ if 90 <= code <= 97:
                fg = code - 90 + 8
            case _ if 100 <= code <= 107:
                bg = code - 100 + 8
            case _:
                # Blink, fonts, ... have no meaning in HTML.
                pass

    return (fg, bg, bold, dim, italic, underline, strike, reverse)


def _color_rules(color, prop, class_prefix, classes, styles):
    """Append the class or inline style that renders one color."""
    match color:
        case None:
            return
        # A palette slot becomes a CSS class; anything else is a literal color.
        case int():
            classes.append(f"{class_prefix}{color}")
        case _:
            styles.append(f"{prop}:{color}")


def _build_tag(state):
    """Render a style state as a single opening ``<span>``."""
    fg, bg, bold, dim, italic, underline, strike, reverse = state

    if reverse:
        # Swap the channels, filling unset sides with the terminal's own
        # colors so the swap stays visible.
        fg, bg = (
            bg if bg is not None else "var(--term-bg, #1c1e2f)",
            fg if fg is not None else "var(--term-fg, #b9c0d3)",
        )

    classes = []
    styles = []

    _color_rules(fg, "color", "ansi-fg-", classes, styles)
    _color_rules(bg, "background-color", "ansi-bg-", classes, styles)

    if bold:
        classes.append("ansi-bold")
    if dim:
        classes.append("ansi-dim")
    if italic:
        classes.append("ansi-italic")
    if underline:
        classes.append("ansi-underline")
    if strike:
        classes.append("ansi-strike")

    tag = "<span"
    if classes:
        tag += f' class="{" ".join(classes)}"'
    if styles:
        tag += f' style="{";".join(styles)}"'
    return tag + ">"


def _tag_for(state):
    tag = _TAG_CACHE.get(state)
    if tag is None:
        tag = _build_tag(state)
        if len(_TAG_CACHE) < _MAX_CACHE:
            _TAG_CACHE[state] = tag
    return tag


def _transition(state, raw_params):
    key = (state, raw_params)
    new_state = _TRANSITION_CACHE.get(key)
    if new_state is None:
        new_state = _apply_params(state, raw_params)
        if len(_TRANSITION_CACHE) < _MAX_CACHE:
            _TRANSITION_CACHE[key] = new_state
    return new_state


def strip_ansi(line):
    """Remove every escape sequence and control character from a line."""
    return CONTROL_JUNK_RE.sub("", SGR_SPLIT_RE.sub("", NON_SGR_RE.sub("", line)))


def has_ansi(line):
    """Check whether a line has any escape sequences."""
    return "\x1b" in line


def ansi_to_html(line, text_filter=None, skip_chars=0):
    """Convert a raw console line into HTML, preserving its ANSI colors.

    Each run of text gets its own self-contained span, so styling never leaks
    past the end of the line.

    :param line: The raw line, exactly as the server printed it.
    :param text_filter: Optional callable applied to each escaped run of text,
        used for keyword highlighting. It is never handed generated markup.
    :param skip_chars: How many leading characters of text to drop. Their
        escape sequences still apply, so the rest keeps the server's color.
    :return: An HTML fragment.
    """
    line = NON_SGR_RE.sub("", line)
    parts = SGR_SPLIT_RE.split(line)

    out = []
    state = DEFAULT_STATE
    skip = skip_chars

    for index, part in enumerate(parts):
        # Odd positions are the captured parameter strings.
        if index % 2:
            state = _transition(state, part)
            continue

        if not part:
            continue

        text = CONTROL_JUNK_RE.sub("", part)
        if skip:
            dropped = min(skip, len(text))
            skip -= dropped
            text = text[dropped:]

        chunk = html.escape(text)
        if not chunk:
            continue
        if text_filter is not None:
            chunk = text_filter(chunk)

        if state == DEFAULT_STATE:
            out.append(chunk)
        else:
            out.extend((_tag_for(state), chunk, "</span>"))

    return "".join(out)
