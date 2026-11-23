"""Find bare URLs in console output and mark them up as guarded links.

Works on text that is already HTML escaped, so the patterns match entities
rather than the punctuation they stand for, and on one run of text at a
time, so a URL a server recolors midway is linked in pieces.
"""

import re

# What a URL may contain (RFC 3986), minus the quotes that would let one
# break out of the href if it ever reached here unescaped. Escaped text
# only holds entities, whose characters are all in the set.
URL_CHARS = r"[\w\-.~:/?#\[\]@!$&()*+,;=%]"

# Requires a scheme or a "www." host. The lookbehind keeps the match off the
# tail of a word or an email address.
URL_RE = re.compile(rf"(?<![\w@.+-])(?:https?://|www\.){URL_CHARS}+", re.IGNORECASE)

# A trimmed match still has to be a URL, not a bare scheme.
VALID_RE = re.compile(rf"(?:https?://|www\.){URL_CHARS}", re.IGNORECASE)

# Entities that sit around a URL, never inside one.
TRAILING_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|quot|#x27|#39);$")

# Punctuation a sentence puts after a URL.
TRAILING_PUNCTUATION = ".,;:!?"

# Markup the log highlighter has already added.
TAG_RE = re.compile(r"<[^>]*>")

LINK_TEMPLATE = (
    '<a href="{href}" class="term-link" target="_blank" '
    'rel="noopener noreferrer nofollow">{text}</a>'
)


def _identity(text):
    return text


def _trim(url):
    """Drop trailing punctuation and entities a URL only borrowed.

    Each pass is O(1), so a token of nothing but ")" cannot cost O(n^2).
    """
    opened = url.count("(")
    closed = url.count(")")

    while url:
        # Guarded so the scan only runs on something that could be an entity.
        trimmed = url
        if trimmed.endswith(";"):
            trimmed = TRAILING_ENTITY_RE.sub("", trimmed)

        trimmed = trimmed.rstrip(TRAILING_PUNCTUATION)

        # Wiki links carry parentheses, so only give back an unopened one.
        if trimmed.endswith(")") and opened < closed:
            trimmed = trimmed[:-1]
            closed -= 1

        if trimmed == url:
            break
        url = trimmed

    return url


def _href(url):
    """Build the href, giving a "www." host a scheme."""
    if url.lower().startswith("www."):
        return f"https://{url}"
    return url


def linkify(escaped_text, text_filter=None):
    """Wrap every bare URL in a run of escaped console text in an anchor.

    :param escaped_text: One run of console text, already HTML escaped.
    :param text_filter: Applied to the text around the links. URLs are kept
        out of it so nothing can be injected inside one.
    :return: An HTML fragment.
    """
    if text_filter is None:
        text_filter = _identity

    out = []
    position = 0

    for match in URL_RE.finditer(escaped_text):
        url = _trim(match.group())
        if not VALID_RE.match(url):
            continue

        if match.start() > position:
            out.append(text_filter(escaped_text[position : match.start()]))

        # _trim only cuts from the end, so the match still starts here.
        out.append(LINK_TEMPLATE.format(href=_href(url), text=url))
        position = match.start() + len(url)

    if not out:
        return text_filter(escaped_text)

    if position < len(escaped_text):
        out.append(text_filter(escaped_text[position:]))

    return "".join(out)


def apply_to_text(fragment, text_filter):
    """Run a text filter over an HTML fragment, skipping the markup in it.

    Only the text between the tags came from the server.

    :param fragment: An HTML fragment.
    :param text_filter: Applied to each run of text between tags.
    :return: The fragment with its text runs replaced.
    """
    out = []
    position = 0

    for match in TAG_RE.finditer(fragment):
        if match.start() > position:
            out.append(text_filter(fragment[position : match.start()]))
        out.append(match.group())
        position = match.end()

    if position < len(fragment):
        out.append(text_filter(fragment[position:]))

    return "".join(out)
