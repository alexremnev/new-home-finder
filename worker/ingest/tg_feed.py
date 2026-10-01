from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from worker.units import sqft_from

LABELS = (
    "Location", "Postcode", "Address", "Price", "Bedrooms",
    "Bathrooms", "Size", "Available", "Furnishing", "Deposit",
)

FIELD = re.compile(
    r"^\W*\*{0,2}(" + "|".join(LABELS) + r")\*{0,2}\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)

MARKDOWN_LINK = re.compile(r"\s*📍?\s*\[[^\]]*\]\([^)]*\)\s*")

# The listing id out of a portal url. Each pattern's first group is the id, and
# `listings` is UNIQUE (source_key, external_id), so a pattern that captures the
# wrong number does not fail — it merges flats.
#
# ── what OpenRent's used to capture ──────────────────────────────────────
#
# It was `property-to-rent/[^/]*/(\d+)`, which assumed the id sits one segment
# after `property-to-rent`. It does on the short form the site also serves —
# `/property-to-rent/london/3059105` — and it does not on the form the feed
# actually carries:
#
#   /property-to-rent/london/2-bed-flat-discovery-dock-e14/3059105
#
# There `[^/]*` took `london` and `(\d+)` matched the next digits it could
# reach, which are the bedroom count at the head of the slug. So every 2-bed
# OpenRent listing from the feed arrived as external_id "2": the first one was
# stored, and every one after it hit `ON CONFLICT (source_key, external_id)`,
# updated that row's `last_seen_at` and returned its id — so the flat was never
# stored, and `notifications` being UNIQUE (user_id, listing_id) meant nobody
# was told about it either. Silently, and once per bedroom count for ever.
#
# Studios and rooms escaped that and were lost a different way: their slugs
# start with a word rather than a digit, nothing matched at all, and the
# message was counted unparseable.
#
# So the id is anchored to the END of the path instead of to a position after
# the host. `[^?#]*?` is lazy and `(?:[/?#]|$)` pins the group to the last
# segment, which is where OpenRent puts the id in both forms it serves.
PORTALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rightmove", re.compile(r"rightmove\.co\.uk/properties/(\d+)", re.IGNORECASE)),
    ("zoopla", re.compile(r"zoopla\.co\.uk/to-rent/details/(\d+)", re.IGNORECASE)),
    (
        "openrent",
        re.compile(
            r"openrent\.co\.uk/property-to-rent/[^?#]*?/(\d+)(?:[/?#]|$)",
            re.IGNORECASE,
        ),
    ),
)

MONEY = re.compile(r"£\s*([\d,]+)")
COUNT = re.compile(r"^(\d+)\b")

ORDINAL = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)

OUTWARD = re.compile(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)\s*\d?[A-Z]{0,2}$", re.IGNORECASE)

NOT_STATED = {"", "n/a", "na", "none", "-", "unknown", "tbc"}

@dataclass
class Parsed:

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
    floor_area_sqft: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

class Unparseable(Exception):

    pass
def fields_in(text: str) -> dict[str, str]:

    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = FIELD.match(line.strip())
        if match:
            found[match.group(1).lower()] = match.group(2).strip().strip("*").strip()
    return found

def listing_link(buttons: list[dict[str, Any]] | list[str]) -> tuple[str, str, str]:

    urls: list[str] = []
    for button in buttons or []:
        url = button if isinstance(button, str) else (button.get("url") or "")
        if url:
            urls.append(str(url))

    for url in urls:
        for portal, pattern in PORTALS:
            match = pattern.search(url)
            if match:

                clean = url.split("?")[0].split("#")[0].rstrip("/")
                return portal, match.group(1), clean
    raise Unparseable("no listing link on any button")

def money(value: str | None) -> int | None:
    if not value or value.strip().lower() in NOT_STATED:
        return None
    match = MONEY.search(value)
    return int(match.group(1).replace(",", "")) if match else None

def bedrooms_of(value: str | None) -> tuple[int, str | None]:

    text = (value or "").strip().lower()
    if not text or text in NOT_STATED:
        raise Unparseable("bedrooms not stated")
    if "studio" in text:
        # A studio is a flat with no separate bedroom, so that is what is
        # stored: the type is "flat" and the bedroom count is nought. Storing
        # "studio" as its own type made it invisible to a filter for flats,
        # which is the filter anybody looking for a studio also ticks.
        return 0, "flat"
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

    return "unknown"

def available_from(value: str | None, *, received_at: datetime | None) -> date | None:

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

    head = text.split()[0]
    return head if OUTWARD.match(text) or OUTWARD.match(head) else None

def parse(
    text: str,
    buttons: list[dict[str, Any]] | list[str] | None = None,
    *,
    received_at: datetime | None = None,
    message_id: int | None = None,
) -> Parsed:

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
        # The Size field, read as an area rather than kept as prose. It is still
        # in `raw` below, because the string says things the number cannot —
        # "approx", or a range.
        floor_area_sqft=sqft_from(values.get("size")),

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
