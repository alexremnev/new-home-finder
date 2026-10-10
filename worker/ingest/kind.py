"""The property type, read off the listing page's own title.

Why this exists at all. A listing that reached us through the Telegram feed
has no property type: the feed states "Bedrooms: 2" and stops, and there is no
Type field in the message at all — see `worker.ingest.tg_feed.LABELS`. So the
alert said "2 Bedrooms" where a scraped one says "2 Bedrooms · Flat", and,
worse, `match._check_property_type` passes a listing whose type is unknown, so
everybody who had asked for a flat was being sent houses and rooms too.

The portals state the type in the one place every link preview reads. Taken
from live pages on 10 October 2026, with the same plain client this uses:

    Rightmove  og:title  "Check out this 2 bedroom apartment for rent on
                          Rightmove"                      → flat
    OpenRent   og:title  "Room in a Shared Flat, Willis House, E14" → room
                         "3 Bed Flat, Hale Street, E14"             → flat

and the head of that document is already being fetched for the photograph
(`worker.ingest.photo`), so for the listings the image step looks at this costs
no request of its own. The ones it skips — it skips any listing whose feed
message carried a picture — cost one GET each, bounded by `parse.TYPE_BATCH`.

Zoopla is the exception and not a fixable one here: a plain client gets 403
and a Cloudflare interstitial, which is also why `fill_images` has never found
a Zoopla picture. Its listings get their type the other way, from the
`zoopla_london` scraper writing into the row the feed created — see the
`COALESCE` in `store.insert_listing`.

── why only the head of the title ───────────────────────────────────────────

Because everything after "to rent" is an address, and an address is prose this
has no business reading. `KINDS` is matched in order, and a title like
"3 bedroom flat to rent in Studio Court, Terrace Road" would otherwise be read
word by word until something stuck. Cutting at "to rent" leaves exactly the
phrase the portal wrote to name the type, which is the only part that is
evidence.

Nothing here guesses. A title that names none of the four words the filter
offers leaves the type as it was — unknown and honest — and
`store.set_listing_type` still records that we looked, so no page is fetched
twice.
"""

from __future__ import annotations

import html as html_entities
import re

# The same two shapes `photo` needs, for the same reason: `property=` and
# `name=` both appear in the wild, in either attribute order.
OG_TITLE = re.compile(
    r"""<meta[^>]+?
        (?:property|name)\s*=\s*["'](?:og:title|twitter:title)["']
        [^>]*?content\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE | re.VERBOSE,
)
OG_TITLE_REVERSED = re.compile(
    r"""<meta[^>]+?content\s*=\s*["']([^"']*)["'][^>]*?
        (?:property|name)\s*=\s*["'](?:og:title|twitter:title)["']""",
    re.IGNORECASE | re.VERBOSE,
)
DOC_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

SPACES = re.compile(r"\s+")

# Where the type stops being stated and the address starts. "For sale" is in
# there because a portal occasionally serves a sales page under a rental url,
# and the phrase before it is still a property type.
LISTED_AS = re.compile(r"\b(?:to|for)\s+(?:rent|let|sale)\b", re.IGNORECASE)

# The portal's own name, tacked on the end. Harmless to `kind_of` — none of
# them contains a type word — but dropped so that what is matched is only what
# the page said about the property.
BRANDING = re.compile(
    "\\s*[|\u2013\u2014-]\\s*(?:rightmove|zoopla|openrent)\\s*$", re.IGNORECASE
)

def title_in(page: str) -> str | None:
    """The page's title: og:title for preference, the `<title>` element after."""

    for pattern in (OG_TITLE, OG_TITLE_REVERSED, DOC_TITLE):
        found = pattern.search(page or "")
        if found:
            text = SPACES.sub(" ", html_entities.unescape(found.group(1))).strip()
            text = BRANDING.sub("", text).strip()
            if text:
                return text
    return None

def said_type(title: str | None) -> str:
    """The part of a title that names the type, with the address dropped."""

    text = (title or "").strip()
    if not text:
        return ""
    cut = LISTED_AS.search(text)
    if cut:
        return text[: cut.start()].strip(" ,-\u2013\u2014")
    # No "to rent" anywhere, which is OpenRent's shape: "2 Bed Flat, Discovery
    # Dock East, London, E14". The first comma separates the type from the
    # address there just as plainly.
    return text.split(",", 1)[0].strip()

def type_in(page: str) -> str | None:
    """One of the four words the filter offers, or None if the page named none.

    The vocabulary is `worker.sources.rightmove.KINDS`, which is a list of
    English words for dwellings rather than anything Rightmove-specific — and
    is matched room-first, so "room in a shared house" is a room. Imported
    inside the call because that module pulls in the scraping stack, which the
    delivery host does not install.
    """

    from worker.sources.rightmove import kind_of

    return kind_of(said_type(title_in(page)))

__all__ = ["BRANDING", "DOC_TITLE", "LISTED_AS", "said_type", "title_in", "type_in"]
