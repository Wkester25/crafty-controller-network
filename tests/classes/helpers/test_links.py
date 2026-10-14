"""Tests for the console URL linkifier."""

import html

from app.classes.helpers import links


def anchor(href, text=None):
    return links.LINK_TEMPLATE.format(href=href, text=text if text else href)


def test_plain_line_is_untouched() -> None:
    line = "[16:58:26 INFO]: Loaded 7 recipes"
    assert links.linkify(line) == line


def test_https_url_becomes_a_link() -> None:
    assert links.linkify("Go to https://example.com now") == (
        f"Go to {anchor('https://example.com')} now"
    )


def test_www_host_gets_a_scheme() -> None:
    assert links.linkify("www.example.com") == (
        anchor("https://www.example.com", "www.example.com")
    )


def test_query_string_entities_stay_in_the_url() -> None:
    escaped = html.escape("https://example.com/a?x=1&y=2")
    assert links.linkify(escaped) == anchor(escaped)


def test_trailing_sentence_punctuation_is_left_out() -> None:
    assert links.linkify("see https://example.com/eula.") == (
        f"see {anchor('https://example.com/eula')}."
    )


def test_bracketing_entities_are_left_out() -> None:
    assert links.linkify(html.escape("<https://example.com>")) == (
        f"&lt;{anchor('https://example.com')}&gt;"
    )


def test_balanced_parentheses_stay_in_the_url() -> None:
    url = "https://example.com/wiki/Thing_(disambiguation)"
    assert links.linkify(url) == anchor(url)


def test_unbalanced_closing_parenthesis_is_left_out() -> None:
    assert links.linkify("(https://example.com/a)") == (
        f"({anchor('https://example.com/a')})"
    )


def test_email_addresses_are_not_linked() -> None:
    line = "admin@www.example.com"
    assert links.linkify(line) == line


def test_url_inside_a_word_is_not_linked() -> None:
    line = "notawww.example.com"
    assert links.linkify(line) == line


def test_several_urls_in_one_line() -> None:
    rendered = links.linkify("a https://one.test b https://two.test c")
    assert rendered == (
        f"a {anchor('https://one.test')} b {anchor('https://two.test')} c"
    )


def test_text_filter_never_sees_the_url() -> None:
    seen = []

    def spy(text):
        seen.append(text)
        return text.upper()

    rendered = links.linkify("go to https://example.com ok", text_filter=spy)

    assert seen == ["go to ", " ok"]
    assert rendered == f"GO TO {anchor('https://example.com')} OK"


def test_text_filter_runs_on_a_line_with_no_url() -> None:
    assert links.linkify("plain", text_filter=str.upper) == "PLAIN"


def test_apply_to_text_skips_markup() -> None:
    fragment = '<span class="mc-log-info">[INFO]</span> www.example.com'
    assert links.apply_to_text(fragment, str.upper) == (
        '<span class="mc-log-info">[INFO]</span> WWW.EXAMPLE.COM'
    )


def test_apply_to_text_leaves_a_bare_fragment_alone() -> None:
    assert links.apply_to_text("no tags", str.upper) == "NO TAGS"


def test_a_bare_scheme_is_not_linked() -> None:
    line = "https://."
    assert links.linkify(line) == line


def test_a_quote_cannot_break_out_of_the_href() -> None:
    """The ?raw=true API path hands log_colors unescaped server output."""
    rendered = links.linkify('https://x.test/"onmouseover="alert(1)')
    assert rendered.startswith(anchor("https://x.test/"))
    assert 'href="https://x.test/"onmouseover' not in rendered


def test_an_apostrophe_ends_the_url() -> None:
    assert links.linkify("https://x.test/a'b") == f"{anchor('https://x.test/a')}'b"
