"""Turning a message from a Telegram listing feed into a listing row.

A pure function over the message text and its buttons: no database, no network, no
clock beyond the message's own timestamp. That is what lets it be tested against
real messages captured with `tools/tg-mirror/mirror.py dump --json`, which is the
only honest way to write a parser for prose written by someone else.

The feed itself is never named here. Which chat is read is configuration — see
`TG_WATCH` in the reader's untracked `.env` — and the parser only needs the shape.

── the shape it reads ────────────────────────────────────────────────────────

    🚀 A listing matching your criteria has just been posted.
    🏡 **Location**: Leytonstone
    📮 **Postcode**: E11 4EG
    🧭 **Address**: Grove Green Road, Leyton E11 📍 [View on map](https://…)
    💰 **Price**: £1700/month
    🛏 **Bedrooms**: 2 Bedrooms
    🛁 **Bathrooms**: 1 Bathroom
    📏 **Size**: N/A
    📅 **Available**: Immediately
    🛋 **Furnishing**: Unfurnished
    🔒 **Deposit**: £1961
    ⚠️ **You're missing out on 90% …**

with the listing's real address on an inline button:

    🏠 View Listing  ->  https://www.rightmove.co.uk/properties/92052438
                        https://www.zoopla.co.uk/to-rent/details/73991134

── two decisions worth stating ───────────────────────────────────────────────

The label is matched leniently — optional emoji, zero to two asterisks, any
spacing — because the source's own markdown is inconsistent: it sends `**Label**`
as literal text *and* as a bold entity, and the emoji is sometimes missing. The
value is whatever follows the colon. Anything stricter would break on the next
cosmetic change, and anything looser would match prose.

The listing is identified by the *portal*, not by the message. The button carries
`rightmove.co.uk/properties/92052438`, so the row is written with
`source_key='rightmove'` and `external_id='92052438'`, and the existing
`UNIQUE (source_key, external_id)` on `listings` then deduplicates three things
for free: the same listing sent twice by the feed, the same listing seen by a
second reader account, and the same listing found later by our own scraper. The
provenance goes in `raw` instead of into the identity.

── what is refused ──────────────────────────────────────────────────────────

`listings` requires a url, a price and a bedroom count. A message missing any of
them is returned as unparseable rather than defaulted: the matcher treats a
present criterion as a requirement, so a guessed price is an alert somebody cannot
act on, which is worse than an alert that never arrives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# The labels this parser knows, in the order the source sends them. Order is not
# relied on for parsing — only for keeping this list readable next to a real
# message.
LABELS = (
    "Location", "Postcode", "Address", "Price", "Bedrooms",
    "Bathrooms", "Size", "Available", "Furnishing", "Deposit",
)

# Leading `\W*` swallows the emoji and any stray punctuation; `\*{0,2}` tolerates
# the source's literal asterisks whether it sends none, one or two.
FIELD = re.compile(
    r"^\W*\*{0,2}(" + "|".join(LABELS) + r")\*{0,2}\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)

# `[View on map](https://…)` is literal markdown inside the Address value, and the
# map link is not the listing. Removed rather than parsed.
MARKDOWN_LINK = re.compile(r"\s*📍?\s*\[[^\]]*\]\([^)]*\)\s*")

PORTALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rightmove", re.compile(r"rightmove\.co\.uk/properties/(\d+)", re.IGNORECASE)),
    ("zoopla", re.compile(r"zoopla\.co\.uk/to-rent/details/(\d+)", re.IGNORECASE)),
    ("openrent", re.compile(r"openrent\.co\.uk/property-to-rent/[^/]*/(\d+)", re.IGNORECASE)),
)

MONEY = re.compile(r"£\s*([\d,]+)")
COUNT = re.compile(r"^(\d+)\b")
# "13th October 2026" — the ordinal suffix has to go before strptime sees it.
ORDINAL = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
# Outward code: the part before the space in "E11 4EG", "SW17 8BW", "HA1 1EH".
OUTWARD = re.compile(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)\s*\d?[A-Z]{0,2}$", re.IGNORECASE)

NOT_STATED = {"", "n/a", "na", "none", "-", "unknown", "tbc"}


@dataclass
class Parsed:
    """Ready to become a `listings` row, plus what could not be expressed there."""

    source_key: str
    external_id: str
    url: str
    price_pcm: int
    bedrooms: int
    bathrooms: int | None = None
    property_type: str | None = None
    furnished: str = "unknown"
    available_from: date | None = None
    postcode: str | None = None
    postcode_district: str | None = None
    deposit_pcm: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Unparseable(Exception):
    """The message is not a listing, or is missing something `listings` requires.

    Carries the reason so it can be stored on the row: a rising count of one
    particular reason is how a format change announces itself.
    """


def fields_in(text: str) -> dict[str, str]:
    """Every `Label: value` pair in the message, keyed by lower-case label."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = FIELD.match(line.strip())
        if match:
            found[match.group(1).lower()] = match.group(2).strip()
    return found


def listing_link(buttons: list[dict[str, Any]] | list[str]) -> tuple[str, str, str]:
    """The portal, its id, and the canonical url, from the message's buttons.

    Taken from the buttons and never from the text: the only url in the text is a
    Google Maps pin, and mistaking it for the listing would produce rows that all
    point at a map.
    """
    urls: list[str] = []
    for button in buttons or []:
        url = button if isinstance(button, str) else (button.get("url") or "")
        if url:
            urls.append(str(url))

    for url in urls:
        for portal, pattern in PORTALS:
            match = pattern.search(url)
            if match:
                # Query and fragment dropped: the same listing arrives with
                # tracking parameters that would otherwise make it look new.
                clean = url.split("?")[0].split("#")[0].rstrip("/")
                return portal, match.group(1), clean
    raise Unparseable("no listing link on any button")


def money(value: str | None) -> int | None:
    if not value or value.strip().lower() in NOT_STATED:
        return None
    match = MONEY.search(value)
    return int(match.group(1).replace(",", "")) if match else None


def bedrooms_of(value: str | None) -> tuple[int, str | None]:
    """The count, and the property type when the count implies one.

    "Studio" is zero bedrooms *and* a property type: the matcher's bedroom range
    treats a studio as 0, and the renderer says "studio" rather than "0 bedrooms".
    """
    text = (value or "").strip().lower()
    if not text or text in NOT_STATED:
        raise Unparseable("bedrooms not stated")
    if "studio" in text:
        return 0, "studio"
    if "room in a share" in text or text.startswith("room"):
        return 1, "room"
    match = COUNT.match(text)
    if match:
        return int(match.group(1)), None
    raise Unparseable(f"could not read bedrooms from {value!r}")


def count_of(value: str | None) -> int | None:
    text = (value or "").strip().lower()
    if not text or text in NOT_STATED:
        return None
    match = COUNT.match(text)
    return int(match.group(1)) if match else None


def furnishing_of(value: str | None) -> str:
    text = (value or "").strip().lower()
    if "unfurnished" in text:
        return "unfurnished"
    if "part" in text:
        return "part"
    if "furnished" in text:
        return "furnished"
    # Deliberately not defaulted to unfurnished: the matcher requires a known
    # value when the criterion is set, and "unknown" is the truthful answer.
    return "unknown"


def available_from(value: str | None, *, received_at: datetime | None) -> date | None:
    """"Immediately" resolves to the day the message was sent, not to today.

    Parsing an old message must not claim it is available now — and a filter that
    asks "available before the 1st" needs a date it can compare, so leaving
    "Immediately" as unknown would silently exclude every one of them.
    """
    text = (value or "").strip()
    if not text or text.lower() in NOT_STATED:
        return None
    if "immediate" in text.lower() or "now" == text.lower():
        return received_at.date() if received_at else None
    cleaned = ORDINAL.sub(r"\1", text.replace("from", "", 1)).strip()
    for pattern in ("%d %B %Y", "%d %b %Y", "%B %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    return None


def district_of(postcode: str | None) -> str | None:
    text = (postcode or "").strip().upper()
    if not text or text.lower() in NOT_STATED:
        return None
    # The outward code is what subscriptions name, so a full postcode is reduced
    # to it and a bare outward code is accepted as it is.
    head = text.split()[0]
    return head if OUTWARD.match(text) or OUTWARD.match(head) else None


def parse(
    text: str,
    buttons: list[dict[str, Any]] | list[str] | None = None,
    *,
    received_at: datetime | None = None,
    message_id: int | None = None,
) -> Parsed:
    """One message, as a listing. Raises `Unparseable` with a reason."""
    values = fields_in(text)
    if not values:
        raise Unparseable("no labelled fields; not a listing message")

    portal, external_id, url = listing_link(buttons or [])

    price = money(values.get("price"))
    if price is None:
        raise Unparseable("price not stated")

    bedrooms, implied_type = bedrooms_of(values.get("bedrooms"))
    postcode = (values.get("postcode") or "").strip() or None
    if postcode and postcode.lower() in NOT_STATED:
        postcode = None

    address = MARKDOWN_LINK.sub(" ", values.get("address") or "").strip(" ·,")

    return Parsed(
        source_key=portal,
        external_id=external_id,
        url=url,
        price_pcm=price,
        bedrooms=bedrooms,
        bathrooms=count_of(values.get("bathrooms")),
        property_type=implied_type,
        furnished=furnishing_of(values.get("furnishing")),
        available_from=available_from(values.get("available"), received_at=received_at),
        postcode=postcode,
        postcode_district=district_of(postcode),
        deposit_pcm=float(money(values.get("deposit")) or 0) or None,
        # Everything the message said that `listings` has no column for, plus where
        # it came from. Kept because a parser improved later can be re-run over it.
        raw={
            "via": "tg_feed",
            "message_id": message_id,
            "location": values.get("location"),
            "address": address or None,
            "size": None if (values.get("size") or "").lower() in NOT_STATED
                    else values.get("size"),
            "fields": values,
        },
    )
