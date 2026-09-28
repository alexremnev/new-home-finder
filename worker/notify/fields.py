from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from worker.contracts.notify import ListingView


@dataclass(frozen=True)
class Line:

    icon: str
    text: str
    bold: bool = False
    link: str | None = None

def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"

def money(amount: int) -> str:
    return f"£{amount:,}".replace(",", ",")

def long_date(value: date) -> str:

    return f"{value.day} {value:%B %Y}"

def maps_link(view: ListingView) -> str | None:

    if not view.postcode:
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + quote(view.postcode)

def size_of(text: str | None) -> str | None:

    if not text:
        return None
    return re.sub(
        r"(\d+(?:\.\d+)?)\s*sq\.?\s*m\b",
        lambda m: f"{round(float(m.group(1)))} m²",
        text,
        flags=re.IGNORECASE,
    )

# What the four stored words look like in a message. `room` never reaches
# here — it has its own line — and anything else is a type this project does
# not recognise, which is left out rather than guessed at.
KINDS = {"flat": "Flat", "house": "House"}


def kind_word(property_type: str | None) -> str | None:
    """The property type as it should read in an alert, or None to omit it."""

    return KINDS.get((property_type or "").strip().lower())


def listing_fields(view: ListingView) -> list[Line]:

    lines = [Line("🏠", "New listing spotted!", bold=True)]

    where = ", ".join(part for part in (view.area, view.address) if part)
    if where:
        lines.append(Line("📍", where))

    link = maps_link(view)
    if view.postcode and link:
        lines.append(Line("📮", view.postcode, link=link))
    elif view.district:
        lines.append(Line("📮", view.district))

    lines.append(Line("💷", f"{money(view.price_pcm)}/month", bold=True))

    # The type goes on the bedroom line rather than a line of its own: it is
    # one word, and an alert that is read on a phone in a hurry is better for
    # being one line shorter.
    #
    # Only when it is known. A listing that reached us through the Telegram
    # feed usually has no type at all — the feed states "2 bedroom" and stops,
    # so `tg_feed.bedrooms_of` returns None for anything that is not a studio
    # or a room — and "2 Bedrooms · Unknown" would be worse than silence. The
    # scrapers read it off the portal, so theirs have it.
    if view.property_type == "room":
        lines.append(Line("🛏️", "Room in a shared flat"))
    elif view.bedrooms == 0:
        # A studio is a flat with no separate bedroom, so the word already
        # says the type. "Studio · Flat" says it twice.
        lines.append(Line("🛏️", "Studio"))
    else:
        beds = plural(view.bedrooms, "Bedroom")
        kind = kind_word(view.property_type)
        lines.append(Line("🛏️", f"{beds} · {kind}" if kind else beds))
    if view.bathrooms:
        lines.append(Line("🛁", plural(view.bathrooms, "Bathroom")))

    size = size_of(view.size_text)
    if size:
        lines.append(Line("📐", size))
    if view.available_from is not None:
        lines.append(Line("📅", "Available from " + long_date(view.available_from)))

    if view.pets_allowed:
        lines.append(Line("🐾", "Pets allowed"))
    if view.furnished and view.furnished != "unknown":
        lines.append(Line("🛋", view.furnished.capitalize()))

    return lines

# The emphasised sentence and what follows it, without the padlock or any
# markup: which characters mean "bold" is the channel's business, not this
# module's, and the two channels disagree.
def restriction_text(view: ListingView) -> tuple[str, str] | None:

    if view.share is None or view.share >= 100:
        return None
    if view.lapsed == "trial":
        return (
            f"Your free trial has ended. You are currently receiving only "
            f"{view.share}% of available properties. Please make a payment to "
            f"restore full access.",
            "",
        )
    return (
        f"Your plan has ended. Access is now limited to {view.share}% of "
        f"property listings. Upgrade today for full access",
        f" — you are missing {100 - view.share}% of what matches.",
    )

__all__ = [
    "KINDS",
    "Line",
    "kind_word",
    "listing_fields",
    "long_date",
    "maps_link",
    "money",
    "plural",
    "restriction_text",
    "size_of",
]
