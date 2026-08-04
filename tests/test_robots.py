from __future__ import annotations

import pytest

from worker.fetch.robots import RobotsTxt

UA = "LondonRentAlerts/0.1 (+https://github.com/alexremnev/new-home-finder)"

# Shapes taken from the real files of the candidate sources, reduced to the rules
# that matter. The probe reads the live files; these fixtures pin the matching.
RIGHTMOVE = """User-agent: *
Disallow: /property-for-sale/contactBranch.html*
Disallow: /property-to-rent/contactBranch.html*
Disallow: /api/*
Disallow: /user/*

User-agent: TrovitBot
Disallow: /

User-agent: GPTBot
Disallow: /
Allow: /mortgages/
"""

ZOOPLA = """User-agent: *
Disallow: /to-rent/fees/*
Disallow: /to-rent/offices/*
Disallow: */details/print/
Disallow: */details/photos/
Disallow: /search/
Crawl-delay: 2

User-agent: Yandex
Disallow: /
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/properties/12345", True),
        ("/property-to-rent/London.html", True),
        # Wildcards are the reason this module exists: the standard library reads
        # `/api/*` as a literal path and would allow these.
        ("/api/x", False),
        ("/user/profile", False),
        ("/property-to-rent/contactBranch.html?a=1", False),
    ],
)
def test_rightmove_rules(path: str, allowed: bool) -> None:
    assert RobotsTxt.parse(RIGHTMOVE).can_fetch(UA, path) is allowed


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/to-rent/details/12345", True),
        ("/to-rent/property/london/", True),
        ("/to-rent/fees/anything", False),
        ("/search/", False),
        # A wildcard spans any characters, not one path segment: `*/details/photos/`
        # requires those segments to be adjacent. Reading it as a segment wildcard
        # would deny more than the site asked for.
        ("/to-rent/details/photos/", False),
        ("/to-rent/details/12345/photos/", True),
    ],
)
def test_zoopla_rules(path: str, allowed: bool) -> None:
    assert RobotsTxt.parse(ZOOPLA).can_fetch(UA, path) is allowed


def test_segment_wildcard_needs_two_stars() -> None:
    robots = RobotsTxt.parse("User-agent: *\nDisallow: */details/*/photos/\n")
    assert robots.can_fetch(UA, "/to-rent/details/12345/photos/") is False


def test_named_bot_rules_do_not_apply_to_us() -> None:
    """A group for another crawler must not leak onto our identity."""
    robots = RobotsTxt.parse(RIGHTMOVE)
    assert robots.can_fetch(UA, "/properties/1") is True
    assert robots.can_fetch("TrovitBot/1.0", "/properties/1") is False
    assert robots.can_fetch("GPTBot", "/properties/1") is False
    assert robots.can_fetch("GPTBot", "/mortgages/calculator") is True


def test_longest_matching_token_wins_over_wildcard_group() -> None:
    robots = RobotsTxt.parse(ZOOPLA)
    assert robots.can_fetch("YandexBot/3.0", "/to-rent/details/1") is False
    assert robots.can_fetch(UA, "/to-rent/details/1") is True


def test_allow_wins_a_tie() -> None:
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /a/b\nAllow: /a/b\n")
    assert robots.can_fetch(UA, "/a/b") is True


def test_more_specific_rule_wins() -> None:
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /a/\nAllow: /a/public/\n")
    assert robots.can_fetch(UA, "/a/private") is False
    assert robots.can_fetch(UA, "/a/public/x") is True


def test_end_anchor() -> None:
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /x$\n")
    assert robots.can_fetch(UA, "/x") is False
    assert robots.can_fetch(UA, "/xy") is True


def test_empty_disallow_imposes_nothing() -> None:
    robots = RobotsTxt.parse("User-agent: *\nDisallow:\n")
    assert robots.can_fetch(UA, "/anything") is True


def test_consecutive_user_agents_share_rules() -> None:
    robots = RobotsTxt.parse("User-agent: somebot\nUser-agent: otherbot\nDisallow: /x\n")
    assert robots.can_fetch("somebot/1.0", "/x") is False
    assert robots.can_fetch("otherbot/1.0", "/x") is False
    assert robots.can_fetch(UA, "/x") is True


def test_group_selection_uses_the_product_token_only() -> None:
    """A group must not match on words that merely appear in the User-Agent.

    Our header carries a repository URL, so matching the whole string would let
    unrelated groups apply by coincidence.
    """
    robots = RobotsTxt.parse("User-agent: github\nDisallow: /\n")
    assert "github" in UA.lower()
    assert robots.can_fetch(UA, "/properties/1") is True
    assert RobotsTxt.product_token(UA) == "londonrentalerts"
    assert RobotsTxt.product_token("TrovitBot/1.0") == "trovitbot"


def test_crawl_delay_and_sitemaps() -> None:
    robots = RobotsTxt.parse(ZOOPLA)
    assert robots.crawl_delay(UA) == 2.0
    assert RobotsTxt.parse("Sitemap: https://x/s.xml\n").sitemaps == ["https://x/s.xml"]


def test_absent_file_allows_everything() -> None:
    """An unparsed file must not be read as a prohibition."""
    assert RobotsTxt().can_fetch(UA, "/anything") is True


def test_site_wide_disallow_is_detected() -> None:
    assert RobotsTxt.parse("User-agent: *\nDisallow: /\n").disallows_everything(UA) is True
    assert RobotsTxt.parse(RIGHTMOVE).disallows_everything(UA) is False
