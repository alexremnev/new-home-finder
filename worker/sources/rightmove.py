"""Rightmove, read from its own search pages.

Everything after this module is unchanged: a listing goes into `listings` like
any other, and match, queue and drain never learn where it came from. What is
specific to Rightmove is only how a listing is found, and it turns out to be
far cheaper than OpenRent.

── what is fetched, and why nothing else is ─────────────────────────────────

One page per district: `/property-to-rent/<outcode>.html?sortType=6`. Every
field this project stores is already in that page, inside the
`<script id="__NEXT_DATA__">` block the Next.js front end hydrates itself from
— 24 listings' worth of clean JSON, with no detail page to fetch and no HTML to
parse with regular expressions. Measured on a live E14 page: 25 listings,
`resultCount` 971, and 120KB across the wire.

Checked against `robots.txt` on 26 September 2026: `/property-to-rent/` is
allowed (only `contactBranch.html`, `draw-a-search.html?`, `map.html?` and
`nearby-schools/` are excluded, and none is touched here), `/properties/` is
allowed, and `/api/*` is disallowed — so the internal JSON API this page's own
scripts call is deliberately not used, even though it would be smaller. There
is no `Crawl-delay` for `*`.

`sortType=6` is newest first. That is what makes one page enough: with the
timer running every ten minutes in working hours, 24 newest listings is far
more than one outcode produces, and paging stops as soon as we are behind
the watermark. The district's search is `radius=0.0`, confirmed from
`searchParameters` in the response, so the page holds that outcode and nothing
around it — which is why the searched district can be trusted as the listing's
district even when the address does not state an outcode.

── the full postcode, and where it hides ────────────────────────────────────

The search page states one in `displayAddress` for about a third of its
results and stops at the outward code for the rest. The rest are on the
listing's own page, and finding them took two attempts, so the shape is
recorded here.

It is NOT in `__NEXT_DATA__`: on a detail page that block's `pageProps` is
empty. It is in `window.__PAGE_MODEL`, and the reason searching the markup for
`"outcode"` finds nothing is that the model's payload is a JSON *string* inside
that object — so every key in it is escaped and `"outcode"` never appears
literally in the page at all. That is what made this look impossible.

Inside, the payload is a flattened array: objects reference other entries by
index rather than nesting, so `{"outcode": 42, "incode": 97}` means "entry 42"
and "entry 97". One object carries the address —
`{countryCode, deliveryPointId, displayAddress, incode, outcode, ukCountry}` —
and resolving its two indices gives the postcode. Four for four on live pages
on 1 October 2026.

No general decoder is needed and none is written: finding the one object with
both keys and following two indices is the whole job, and a decoder for a
format nobody documents is a decoder that breaks silently.

── the furnishing, which is in there too ───────────────────────────────────

The same payload carries `lettings`, and `furnishType` in it is Rightmove's own
answer — "Unfurnished" on listing 94293753, measured 15 October 2026. The
search card has no such field: `tags` and `keywords` have never been non-empty
here, so `features` had only the 300-character `summary` and whatever the agent
chose to bullet. On a live E14 page of 25 that gave the furnishing for 11
listings and pets for none at all, and 94293753 was one of the fourteen — it
went out to subscribers who had asked for furnished, because prose called it
unknown and `match._check_furnished` passes unknown to everybody.

So `enrich` reads it, along with the untruncated description that is the only
place pets are ever stated. Unlike Zoopla's, this page is not free: the
postcode above is only wanted for a card that stated none, about two in three,
so the other third is a request that was not being made before. `DETAIL_BUDGET`
is the ceiling, and `details_on` parses the payload once for both answers.

About 100KB a page, spent only on a listing we are about to store that has no
postcode of its own. It buys two things — an alert that names the street, and
a working duplicate rule, since `store.mark_duplicate` compares on the full
postcode and skips any listing without one. Before this, the same flat
advertised on Rightmove and Zoopla was sent twice.

── what this costs, and where the pictures come from ────────────────────────

The preview picture is published as an already-resized CDN variant
(`…_max_476x317.jpeg`) on `media.rightmove.co.uk`, and we never download it: we
store the url, and Telegram and WhatsApp fetch it from their own servers when
the alert goes out. Confirmed with a plain client and a bot user-agent — 200,
no impersonation needed — so it costs no proxy traffic and there is nothing to
re-encode. `media.rightmove.co.uk` belongs in `SCRAPE_DIRECT_HOSTS` all the
same, so that anything that ever does fetch one goes straight out rather than
through paid residential traffic.

One free saving on top: the same listing's first photo came to 141KB as a PNG
and 17KB as a JPEG, so where a listing publishes both, the first JPEG is
preferred over a first-position PNG. Eight times less for the subscriber's
phone to pull, of the same flat. `PREFER_JPEG` turns it off.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from worker.contracts.listing import Furnished, Listing
from worker.obs.log import Stage
from worker.sources import postcode
from worker.sources.fetch import Fetcher
from worker.sources.sweep import Catch, Harvest, Memory
from worker.units import sqft_from

SOURCE_KEY = "rightmove"
BASE = "https://www.rightmove.co.uk"

# Newest listed first. Confirmed against the default ordering: the same page
# sorted this way led with a listing first visible on 25 September where the
# default led with one from 2 September.
NEWEST_FIRST = "6"

# The portal's own paging step. `numberOfPropertiesPerPage` says 24 and the
# response carries 25 — the extra one is a promoted slot — so the step is read
# from their pagination rather than from the length of what came back.
PAGE_STEP = 24

# Pages per district per run. Only ever reached on a district busier than 24
# new listings between two runs, which no London outcode is; it exists so that
# a portal bug cannot turn one district into an unbounded crawl.
MAX_PAGES = 5

# Prefer a JPEG over a first-position PNG. See the module note: same flat,
# eight times smaller. Set False to always take the photograph the agent put
# first, whatever it weighs.
PREFER_JPEG = True

# Listing pages per run, for `Rightmove.enrich`. Lower than
# `sweep.POSTCODE_BUDGET` because unlike Zoopla's this one costs real extra
# requests: the postcode is read from this page only for a card that stated
# none, about two in three, so the remaining third is traffic that was not
# being spent before. A page is about 100KB across the wire.
#
# It is a ceiling and not a target. A district producing more than 25 new
# listings between two runs is already past what one search page carries, and
# the listings past this budget are stored with what the card said — which is
# what every Rightmove listing was stored with before this existed.
DETAIL_BUDGET = 25

SPACES = re.compile(r"\s+")

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

# A full UK postcode at the end of an address — "London, E14 0UY". Rightmove
# states one on some listings and stops at the outcode on others, so this is
# tried and allowed to fail rather than required.
FULL_POSTCODE = re.compile(
    r"\b([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})\s*$", re.IGNORECASE
)

# Every `propertySubType` Rightmove served across eight London outcodes on
# 26 September 2026, which is where this list comes from rather than from
# guesswork: Apartment, Detached, Duplex, End of Terrace, Flat, Flat Share,
# Ground Maisonette, House, House Boat, House Share, Maisonette, Not Specified,
# Parking, Penthouse, Semi-Detached, Studio, Terraced, Town House.
#
# Narrowed to the four words the filter offers, exactly as OpenRent's slug
# reader is: the matcher compares the stored type against the filter as a
# string, so "Semi-Detached" would never answer a filter for "house". The
# portal's own word is kept in `raw`.
#
# Word boundaries, not substrings. "House Share" and "House Boat" both contain
# "house" and neither is one, and a substring match filed both as houses.
KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Before the flat and house patterns: all three names contain one.
    (re.compile(r"\b(?:house|flat|maisonette)\s*share\b", re.I), "room"),
    (re.compile(r"\brooms?\b", re.I), "room"),
    (re.compile(r"\bstudio", re.I), "flat"),
    (re.compile(r"\bapartment", re.I), "flat"),
    (re.compile(r"\bpenthouse\b", re.I), "flat"),
    (re.compile(r"\bmaisonette\b", re.I), "flat"),
    (re.compile(r"\bduplex\b", re.I), "flat"),
    (re.compile(r"\bflats?\b", re.I), "flat"),
    (re.compile(r"\bbungalow", re.I), "house"),
    (re.compile(r"\bterrac", re.I), "house"),
    (re.compile(r"\bdetached\b", re.I), "house"),
    (re.compile(r"\bcottage", re.I), "house"),
    (re.compile(r"\bmews\b", re.I), "house"),
    (re.compile(r"\bvilla\b", re.I), "house"),
    (re.compile(r"\btown\s?house\b", re.I), "house"),
    (re.compile(r"\bhouses?\b", re.I), "house"),
)

# Somewhere to live, but none of the four words the filter offers. Named
# explicitly because "House Boat" — Rightmove's own spelling, with the space —
# carries "House" as a whole word, so even boundary matching files it as a
# house. Stored with no type rather than as the wrong one.
NEITHER = re.compile(
    r"\b(?:boat|barge|houseboat|mobile\s+home|park\s+home|caravan|lodge)\b",
    re.IGNORECASE,
)

# Not somewhere anybody lives, and so not stored at all.
#
# Rightmove's rental channel carries parking spaces, and one came back as
# `Parking` with `bedrooms: 0`. Left in, a £250 parking space would answer a
# studio search — the bedroom count and the rent both fit — and somebody would
# open an alert about a car park. The rest are on the same channel and are the
# same mistake waiting to happen.
NOT_A_DWELLING = re.compile(
    r"\b(?:parking|garage|land|plot|commercial|office|retail|industrial|"
    r"warehouse|block\s+of\s+(?:apartments|flats))\b",
    re.IGNORECASE,
)

FURNISHING: tuple[tuple[Furnished, re.Pattern[str]], ...] = (
    ("part", re.compile(r"\bpart[\s-]*furnished\b", re.IGNORECASE)),
    ("unfurnished", re.compile(r"\bunfurnished\b", re.IGNORECASE)),
    ("furnished", re.compile(r"\bfurnished\b", re.IGNORECASE)),
)

# Rightmove's own machine-readable furnishing, `lettings.furnishType` on the
# listing's page. There is no such field on a search card — see `features` for
# the whole of what a card can be asked.
#
# Matched on a lowercased, space-collapsed value. Only "Unfurnished" has been
# seen on a live page here (listing 94293753, 15 October 2026); the other
# spellings are Rightmove's own filter wording. Anything unrecognised falls
# through to `unknown`, which is the safe direction: a value this map has not
# learned yet leaves the listing answering every furnishing filter, exactly as
# it did before this existed, rather than being filed as the wrong one.
#
# "Ask agent" and "Furnished or unfurnished" are deliberately absent. Both mean
# the portal is not stating one, and `unknown` is what lets
# `match._check_furnished` hand the listing to everybody rather than to half.
FURNISH_TYPES: dict[str, Furnished] = {
    "furnished": "furnished",
    "unfurnished": "unfurnished",
    "part furnished": "part",
    "partly furnished": "part",
}

PETS = re.compile(r"\bpets?\s*(?:allowed|welcome|considered|friendly)\b", re.IGNORECASE)
BILLS = re.compile(r"\bbills?\s*(?:are\s*)?includ", re.IGNORECASE)


def search_url(district: str, index: int = 0) -> str:
    """The search page for one outcode.

    Built from the outcode name rather than Rightmove's internal
    `OUTCODE^749` identifier, which would mean keeping a lookup table of 594
    numbers in step with theirs. Their own sitemap publishes exactly this
    shape.
    """

    url = f"{BASE}/property-to-rent/{district.upper()}.html?sortType={NEWEST_FIRST}"
    return url if index <= 0 else f"{url}&index={index}"


def next_data(page: str) -> dict[str, Any]:
    """The hydration payload, or an empty dict when the page has none."""

    found = NEXT_DATA.search(page)
    if not found:
        return {}
    try:
        parsed = json.loads(found.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def results_in(page: str) -> dict[str, Any]:
    """`searchResults` from one search page."""

    props = next_data(page).get("props")
    if not isinstance(props, dict):
        return {}
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        return {}
    results = page_props.get("searchResults")
    return results if isinstance(results, dict) else {}


def monthly(price: Any) -> int | None:
    """The rent per calendar month, whatever the portal quoted it in."""

    if not isinstance(price, dict):
        return None
    try:
        amount = float(price.get("amount"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

    # Every London listing seen so far quotes monthly, but the field exists and
    # a weekly figure read as monthly would be a quarter of the real rent —
    # which would pass the filter and waste somebody's evening.
    frequency = str(price.get("frequency") or "monthly").lower()
    if frequency == "weekly":
        amount = amount * 52 / 12
    elif frequency in ("yearly", "annually"):
        amount = amount / 12
    elif frequency not in ("monthly", "monthly_rent"):
        return None

    rounded = round(amount)
    # The contract's own bounds. Outside them the number is not a London rent,
    # and storing it would only put an absurd figure in front of somebody.
    return rounded if 100 <= rounded <= 100_000 else None


def when(raw: Any) -> date | None:
    stamp = at(raw)
    return None if stamp is None else stamp.date()


def at(raw: Any) -> datetime | None:
    """An ISO instant from the portal, as an aware datetime."""

    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def kind_of(sub_type: Any) -> str | None:
    """One of the four words the filter offers, or None when unrecognised."""

    words = str(sub_type or "")
    if NEITHER.search(words):
        return None
    for pattern, kind in KINDS:
        if pattern.search(words):
            return kind
    return None


def a_dwelling(sub_type: Any) -> bool:
    """Whether this is somewhere to live. See NOT_A_DWELLING."""

    return not NOT_A_DWELLING.search(str(sub_type or ""))


def said_type(row: dict[str, Any]) -> tuple[str, ...]:
    """What this card says the property is, most authoritative first.

    `propertySubType` leads, and behind it `propertyTypeFullDescription` —
    the phrase Rightmove prints above the price, "2 bedroom flat", "Studio
    flat", "3 bedroom terraced house". It names the type on every card in the
    search payload, including the ones whose sub type is "Not Specified",
    which is the only vocabulary Rightmove serves that `kind_of` cannot read.
    An untyped listing answers every property-type filter — see
    `match._check_property_type` — so somebody who asked for a flat was being
    sent whatever the agent had failed to classify.

    A tuple rather than one joined string, for the reason Zoopla's version of
    this gives: `KINDS` is matched room-first so that "house share" is a room
    and not a house, and against one joined string a card reading `Detached`
    with "room" anywhere in the other field would come back a room. Each
    source is read on its own, in turn, and the first to name a type wins.
    """

    return tuple(
        part
        for part in (
            str(row.get("propertySubType") or ""),
            str(row.get("propertyTypeFullDescription") or ""),
        )
        if part.strip()
    )


def type_of(row: dict[str, Any]) -> str | None:
    """One of the four words the filter offers, from wherever the card says it."""

    for words in said_type(row):
        kind = kind_of(words)
        if kind is not None:
            return kind
    return None


def picture(images: Any) -> str | None:
    """The preview picture's url — a JPEG for preference. See the module note."""

    urls: list[str] = []
    if isinstance(images, dict):
        listed = images.get("images")
        if isinstance(listed, list):
            for one in listed:
                if isinstance(one, dict):
                    src = str(one.get("srcUrl") or "").strip()
                    if src.startswith("https://"):
                        urls.append(src[:1000])
    if not urls:
        return None
    if PREFER_JPEG:
        for one in urls:
            if one.lower().endswith((".jpg", ".jpeg")):
                return one
    return urls[0]


def furnishing_in(text: str) -> Furnished:
    """Which of the three furnishing words `text` states, if any.

    Order matters and `FURNISHING` carries it: "part furnished" contains
    "furnished", and "unfurnished" contains it too, so the longest claim has to
    be tested first or every advert comes back furnished.
    """

    for name, pattern in FURNISHING:
        if pattern.search(text):
            return name
    return "unknown"


def features(row: dict[str, Any]) -> str:
    """The listing's own words, for the few facts only prose states.

    This is the whole of what a search card can be asked, and it is thin.
    `summary` is a truncation of the description — 300 characters, ending in an
    ellipsis on 8 of 25 on a live E14 page — and `keyFeatures` is whatever the
    agent chose to bullet. Neither `tags` nor `keywords` has ever been
    non-empty here. Measured on that page: the furnishing came out for 11 of 25
    and pets for 0 of 25, because the card has no field for either.

    So the card is the fallback and the listing's own page is the answer; see
    `details_on`.
    """

    parts = [str(row.get("summary") or "")]
    listed = row.get("keyFeatures")
    if isinstance(listed, list):
        parts.extend(
            str(one.get("description") or "")
            for one in listed
            if isinstance(one, dict)
        )
    return " · ".join(part for part in parts if part)


def as_listing(row: Any, district: str) -> Listing | None:
    """One search result as a `Listing`, or nothing if it is unusable."""

    if not isinstance(row, dict):
        return None
    if not a_dwelling(row.get("propertySubType")):
        return None
    listing_id = row.get("id")
    price = monthly(row.get("price"))
    if listing_id is None or price is None:
        # Without an id there is nothing to deduplicate against and without a
        # price there is nothing to match on, so it is skipped rather than
        # stored half-formed. The caller counts these.
        return None

    # The fragment is Rightmove's own in-page router state, not part of the
    # address: "/properties/93625281#/?channel=RES_LET". Dropped so that the
    # link in an alert is the plain listing page.
    path = str(row.get("propertyUrl") or f"/properties/{listing_id}").split("#", 1)[0]

    # Whitespace collapsed, not merely stripped. Rightmove embeds newlines in
    # some addresses — "Duke Shore Wharf,\n106 Narrow Street, E14" — and an
    # alert is a list of one-line facts, so a line break in the middle of one
    # of them breaks the shape of the whole message.
    address = SPACES.sub(" ", str(row.get("displayAddress") or "")).strip()
    postcode = None
    stated = FULL_POSTCODE.search(address)
    if stated:
        postcode = " ".join(stated.group(1).upper().split())

    prose = features(row)
    furnished = furnishing_in(prose)

    where = row.get("location")
    where = where if isinstance(where, dict) else {}

    return Listing(
        source_key=SOURCE_KEY,
        external_id=str(listing_id),
        url=f"{BASE}{path}" if path.startswith("/") else path,
        price_pcm=price,
        # A studio already arrives as 0, which is this project's own
        # convention for one — confirmed on a live page, where every
        # `propertySubType: "Studio"` had `bedrooms: 0`.
        bedrooms=_count(row.get("bedrooms"), default=0) or 0,
        bathrooms=_count(row.get("bathrooms")),
        property_type=type_of(row),
        furnished=furnished,
        # True or None, never False: a listing that does not mention pets has
        # not forbidden them, and recording a refusal nobody made would hide
        # flats from the people who need this filter most.
        pets_allowed=True if PETS.search(prose) else None,
        bills_included=True if BILLS.search(prose) else None,
        available_from=when(row.get("letAvailableDate")),
        # Neither the minimum tenancy nor the deposit is in this payload. Both
        # are on the detail page, and neither is worth a request each: the
        # deposit appears in an agent's own words ("DEPOSIT 5 WEEKS (£3461)")
        # often enough that parsing it would be guesswork dressed as data.
        min_tenancy_months=None,
        deposit_pcm=None,
        postcode=postcode,
        # The searched outcode, not the address: the search is radius 0.0, so
        # everything on the page is in this district, and a third of the
        # addresses state no outcode at all.
        postcode_district=district.upper(),
        lat=_coord(where.get("latitude"), 90),
        lng=_coord(where.get("longitude"), 180),
        title=address or None,
        description=str(row.get("summary") or "") or None,
        # Rightmove lists through member agents only; there is no private
        # landlord channel on it.
        is_landlord_direct=False,
        photo_count=_count(row.get("numberOfImages")),
        # Stated as "676 sq. ft." and read for its unit rather than assumed —
        # `searchParameters.areaSizeUnit` says sqft here, but the text is what
        # is parsed, so a page served in metres cannot silently become a
        # listing four times too small. See worker.units.
        floor_area_sqft=sqft_from(str(row.get("displaySize") or "")),
        raw={
            # The alert's own "where" line reads `raw.address`, not the
            # listing title — so without this key a Rightmove alert printed no
            # address at all, while Zoopla's printed one. Same value as
            # `title`, stored under the name the renderer looks for.
            "address": address,
            "property_sub_type": str(row.get("propertySubType") or ""),
            "added_or_reduced": str(row.get("addedOrReduced") or ""),
            "update_reason": str(
                (row.get("listingUpdate") or {}).get("listingUpdateReason") or ""
            ),
            "branch": str(
                (row.get("customer") or {}).get("branchDisplayName") or ""
            ),
        },
    )


def _count(raw: Any, *, default: int | None = None) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _coord(raw: Any, limit: float) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if -limit <= value <= limit else None


def promoted(row: dict[str, Any]) -> bool:
    return bool(row.get("featuredProperty")) or bool(row.get("premiumListing"))


@dataclass(frozen=True)
class Read:
    """One search page, as far as this module is concerned."""

    caught: list[Catch]
    pagination: dict[str, Any]
    #: Rows on the page that were not stored: a parking space, a listing with
    #: no price, a shape we did not recognise. Counted rather than dropped in
    #: silence, because a parser that quietly stops understanding a portal
    #: looks exactly like a portal that has gone quiet.
    skipped: int = 0
    #: Rows the listing contract refused outright. Separate from `skipped`
    #: because this one means our own rules and their data have diverged,
    #: which is worth looking at rather than shrugging at.
    invalid: int = 0


def catches_in(page: str, district: str) -> Read:
    """Every usable listing on one search page, with its pagination."""

    results = results_in(page)
    rows = results.get("properties")
    rows = rows if isinstance(rows, list) else []
    caught: list[Catch] = []
    invalid = 0
    for row in rows:
        try:
            listing = as_listing(row, district)
        except ValidationError:
            # Never fatal. A district is dozens of listings and a run is
            # dozens of districts; one row the contract refuses is not a
            # reason to lose any of the rest. See the same guard in openrent.
            invalid += 1
            continue
        if listing is None:
            continue
        caught.append(
            Catch(
                listing=listing,
                image=picture(row.get("propertyImages") or row.get("images")),
                first_listed=at(row.get("firstVisibleDate")),
                # What `sortType=6` actually orders on. Measured over 75
                # consecutive London results: excluding the promoted rows,
                # `listingUpdateDate` was monotone and `firstVisibleDate` was
                # out of order fourteen times.
                ordered_by=at(
                    (row.get("listingUpdate") or {}).get("listingUpdateDate")
                ),
                promoted=promoted(row),
            )
        )
    pagination = results.get("pagination")
    return Read(
        caught=caught,
        pagination=pagination if isinstance(pagination, dict) else {},
        skipped=len(rows) - len(caught) - invalid,
        invalid=invalid,
    )


PAGE_MODEL = "window.__PAGE_MODEL"

# The one object in the flattened payload that carries an address. Matched on
# its keys rather than its position, because the position is an artefact of
# whatever the page happened to render.
ADDRESS_KEYS = ("outcode", "incode")

# The same trick for the letting details, which is where the furnishing is:
# `{letAvailableDate, deposit, minimumTermInMonths, letType, furnishType}`.
# Two keys rather than one, so that the match is about this object and not
# about a field name that might appear anywhere.
LETTINGS_KEYS = ("furnishType", "letType")


def page_model(page: str) -> dict[str, Any]:
    """`window.__PAGE_MODEL` as a dict, or empty when it is not there.

    Read by balancing braces rather than with a regular expression: the
    assignment is followed by a hundred kilobytes of JSON containing every
    bracket there is, and a greedy or lazy pattern gets either far too much or
    far too little.
    """

    at = page.find(PAGE_MODEL)
    if at < 0:
        return {}
    start = page.find("{", at)
    if start < 0:
        return {}

    depth = 0
    inside = False
    escaped = False
    for here in range(start, len(page)):
        char = page[here]
        if inside:
            # Braces inside a string are not structure, and a quote after a
            # backslash does not end the string.
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside = False
            continue
        if char == '"':
            inside = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(page[start : here + 1])
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


def flat_payload(page: str) -> list[Any]:
    """The flattened, index-referencing array inside `window.__PAGE_MODEL`.

    Empty when the page carries none. See the module note for the shape and
    for why no general decoder is written for it.
    """

    payload = page_model(page).get("data")
    if not isinstance(payload, str):
        return []
    try:
        flat = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return flat if isinstance(flat, list) else []


def _reader(flat: list[Any]) -> Callable[[Any], Any]:
    """Resolve one entry of the flattened payload to its value."""

    def at(index: Any) -> Any:
        # An entry is either an index into the array or the value itself.
        if isinstance(index, int) and not isinstance(index, bool):
            return flat[index] if 0 <= index < len(flat) else None
        return index

    return at


def postcode_in(flat: list[Any], district: str) -> str | None:
    """The listing's full postcode, from an already-parsed payload."""

    at = _reader(flat)
    found: set[str] = set()
    for entry in flat:
        if not isinstance(entry, dict):
            continue
        if not all(key in entry for key in ADDRESS_KEYS):
            continue
        outward = at(entry["outcode"])
        inward = at(entry["incode"])
        if not isinstance(outward, str) or not isinstance(inward, str):
            continue
        whole = postcode.tidy(outward, inward)
        if whole:
            found.add(whole)

    wanted = district.strip().upper()
    ours = {one for one in found if one.split(" ")[0] == wanted}
    # Exactly one in the district we already know. None means the page did not
    # say; several means it said more than one thing and guessing between them
    # is how a neighbour's address ends up in somebody's alert.
    return ours.pop() if len(ours) == 1 else None


def postcode_on(page: str, district: str) -> str | None:
    """The listing's full postcode, from its own page. See the module note.

    `district` is the outward code we already know from the search. It is used
    to choose between candidates and to refuse a disagreement: a page can
    carry panels for other properties, and a neighbour's postcode in the alert
    would also put a wrong fingerprint into the duplicate rule.
    """

    return postcode_in(flat_payload(page), district)


def furnish_type_in(flat: list[Any]) -> Furnished:
    """`lettings.furnishType`, Rightmove's own answer, or unknown.

    Found by its keys rather than by position, exactly as `postcode_in` finds
    the address: the position is an artefact of whatever the page rendered.
    `LETTINGS_KEYS` asks for two of them together so that a panel for some
    other property — or a future payload that moves the field — cannot be
    mistaken for this listing's letting details.
    """

    at = _reader(flat)
    for entry in flat:
        if not isinstance(entry, dict):
            continue
        if not all(key in entry for key in LETTINGS_KEYS):
            continue
        stated = at(entry["furnishType"])
        if not isinstance(stated, str):
            continue
        found = FURNISH_TYPES.get(SPACES.sub(" ", stated).strip().lower())
        if found is not None:
            return found
    return "unknown"


def prose_in(flat: list[Any]) -> str:
    """The advert's full description and bullets, untruncated.

    The card carries 300 characters of the first and a copy of the second; this
    is the whole of both, which is the only place Rightmove ever states pets.
    """

    at = _reader(flat)
    parts: list[str] = []
    for entry in flat:
        if not isinstance(entry, dict) or "keyFeatures" not in entry:
            continue
        text = at(entry.get("text"))
        if isinstance(text, dict):
            described = at(text.get("description"))
            if isinstance(described, str):
                parts.append(described)
        bullets = at(entry["keyFeatures"])
        if isinstance(bullets, list):
            parts.extend(one for one in (at(x) for x in bullets) if isinstance(one, str))
    return " · ".join(part for part in parts if part)


@dataclass(frozen=True)
class Details:
    """What only the listing's own page states.

    One parse of a hundred kilobytes of JSON, read once and asked several
    questions, because `enrich` and `postcode_for` both want answers out of the
    same body.
    """

    postcode: str | None = None
    furnished: Furnished = "unknown"
    pets_allowed: bool | None = None


def details_on(page: str, district: str) -> Details:
    """Everything this project stores that only the listing's page states."""

    flat = flat_payload(page)
    if not flat:
        return Details()
    words = prose_in(flat)
    # Structured first, the agent's words second — and spelled out rather than
    # left to `or`, because "unknown" is a non-empty string and would have
    # swallowed the fallback whole.
    #
    # They disagree, and the field is the portal's own answer: 94293753 stated
    # `furnishType: "Unfurnished"` while nothing in its description or its nine
    # bullets mentioned furnishing at all, so prose alone called it unknown and
    # `match._check_furnished` passed it to everybody — including the
    # subscribers who had asked for furnished. Measured 15 October 2026.
    # Rightmove has no tag tier between the two; Zoopla does, and
    # `zoopla.furnishing_on` uses it.
    stated = furnish_type_in(flat)
    return Details(
        postcode=postcode_in(flat, district),
        furnished=stated if stated != "unknown" else furnishing_in(words),
        # Never False, for the reason `as_listing` gives: a page that does not
        # mention pets has not refused them. Rightmove has no structured field
        # for this — checked against every key on a live page — so the advert's
        # own words are the only statement there is.
        pets_allowed=True if PETS.search(words) else None,
    )


def fill(listing: Listing, found: Details) -> Listing:
    """The listing with the two filters a search card cannot answer.

    The page wins wherever it has an answer, and the card is the fallback
    rather than the other way round — see `details_on` for the ordering and
    `features` for why the card is the weaker source. A page that states
    nothing leaves whatever the card managed to find.

    The postcode is not written here on purpose: `postcode_for` owns it, for
    the ordering reason `sweep.collect` gives — it has to be in place before
    `store.mark_duplicate` runs — and `Rightmove.enrich` hands it over rather
    than setting it, so that the engine's own postcode counters and budget keep
    meaning what they say.
    """

    update: dict[str, Any] = {}
    if found.furnished != "unknown":
        update["furnished"] = found.furnished
    if found.pets_allowed is not None:
        update["pets_allowed"] = found.pets_allowed
    return listing.model_copy(update=update) if update else listing


@dataclass
class Rightmove:
    """The portal, as `worker.sources.sweep.collect` needs it.

    Not frozen: `_details` is this run's remaining detail-page budget and
    `enrich` spends it, the same arrangement `spareroom.SpareRoom` uses.
    """

    key: str = SOURCE_KEY
    #: Every listing carries `firstVisibleDate`, so announceability is decided
    #: from the portal's own dates and a district can start being watched on
    #: one page. See the note in worker.sources.sweep.
    dated: bool = True
    max_pages: int = MAX_PAGES

    #: Listing pages per run, for `enrich`. See `DETAIL_BUDGET` for what this
    #: costs, which unlike Zoopla's is not nothing.
    detail_budget: int = DETAIL_BUDGET

    _details: int = field(default=-1, init=False)
    #: What `enrich` read off each listing page, so `postcode_for` can have it
    #: without a second request. Keyed by external id, and absent rather than
    #: None when the page was never read.
    _postcodes: dict[str, str | None] = field(default_factory=dict, init=False)

    def enrich(self, catch: Catch, get: Fetcher, stage: Stage) -> Catch:
        """The two filters a search card cannot answer, from the advert.

        A Rightmove card has no furnishing field and no pets field: the
        furnishing came out for 11 of 25 on a live page and pets for 0 of 25,
        both off prose alone. The page states the first outright and carries
        the untruncated description the second would have to be in — see
        `details_on` and `features`.

        Unlike Zoopla's, this is not free. `postcode_for` reads this page only
        for a listing whose card stated no full postcode, about two in three,
        so for the other third this is a request that was not being made
        before. `DETAIL_BUDGET` is the ceiling on it.
        """

        if self._details < 0:
            self._details = self.detail_budget
        if self._details <= 0:
            stage.count("detail_over_budget")
            return catch
        self._details -= 1

        district = catch.listing.postcode_district or ""
        reply = get.get(catch.listing.url)
        found = details_on(reply.body, district)
        # Kept whatever happens to the rest: a page that says nothing about
        # furnishing may still state the postcode, and `postcode_for` must not
        # be sent back to fetch the same body again.
        if district:
            self._postcodes[catch.listing.external_id] = found.postcode
        try:
            filled = fill(catch.listing, found)
        except ValidationError:
            # The card already validated, so this is the page contradicting it.
            # Keeping the card's version is the conservative answer.
            stage.count("detail_invalid")
            return catch
        stage.count("detail_pages")
        return catch if filled is catch.listing else replace(catch, listing=filled)

    def postcode_for(
        self, catch: Catch, get: Fetcher, stage: Stage
    ) -> str | None:
        """The full postcode, from the listing's own page.

        Only reached for a listing whose search result stated none, which is
        about two in three. See the module note for where it hides and why
        finding it took two attempts.

        Normally free now: `enrich` has already read that page this run and
        left the answer behind. The fetch here is for the listings it did not
        reach — one whose page would not load, and one past `detail_budget` —
        so that the postcode keeps its own budget rather than inheriting
        enrich's.
        """

        del stage
        district = catch.listing.postcode_district or ""
        if not district:
            return None
        # Membership, not the value: "looked and the page did not say" and
        # "never looked" are both None and only the second is worth a request.
        if catch.listing.external_id in self._postcodes:
            return self._postcodes[catch.listing.external_id]
        return postcode_on(get.get(catch.listing.url).body, district)

    def harvest(
        self,
        district: str,
        get: Fetcher,
        stage: Stage,
        since: datetime | None,
        memory: Memory | None = None,
    ) -> Harvest:
        # Unused here: this portal's search page carries the listings
        # themselves, so there is nothing to decide before fetching them.
        del memory
        caught: list[Catch] = []
        seen: set[str] = set()
        pages = 0
        complete = False
        index = 0

        while pages < self.max_pages:
            reply = get.get(search_url(district, index))
            pages += 1
            read = catches_in(reply.body, district)
            found, pagination = read.caught, read.pagination
            if read.skipped:
                stage.count("not_a_listing", read.skipped)
            if read.invalid:
                stage.count("invalid", read.invalid)
            if not found:
                # An empty district, or a page shape we did not recognise.
                # Either way there is nothing above it either, so this is the
                # end rather than a partial read.
                complete = True
                break

            for one in found:
                # `index` steps 24 while a page carries 25, so consecutive
                # pages overlap by the promoted slot.
                if one.listing.external_id not in seen:
                    seen.add(one.listing.external_id)
                    caught.append(one)

            if since is None:
                # Starting to watch this district: one page is all that is
                # wanted. Nothing currently advertised will be announced, so
                # reading the other forty pages of standing stock would be
                # forty requests spent on listings nobody will be told about.
                complete = True
                break

            # Stop when the page has reached listings the portal ordered
            # before the watch began. Two things make this subtler than it
            # looks, and both were measured rather than guessed:
            #
            #  * The order is `listingUpdateDate`, not `firstVisibleDate`.
            #    `sortType=6` is "most recent" and a price cut counts, so a
            #    flat first listed in July and reduced this afternoon sits
            #    near the top. Comparing the wrong field stops paging the
            #    moment one of those appears, which on a busy day is page one.
            #  * Promoted rows are exempt. Rightmove's featured and premium
            #    slots are out of order by construction — over 75 consecutive
            #    results the only three breaks in the ordering were exactly
            #    those rows.
            ordered = [one for one in found if not one.promoted]
            if any(
                one.sort_key is None or one.sort_key <= since for one in ordered
            ):
                complete = True
                break

            nxt = pagination.get("next")
            if nxt is None:
                complete = True
                break
            try:
                index = int(nxt)
            except (TypeError, ValueError):
                index = index + PAGE_STEP

        if not complete:
            stage.log(
                "warn",
                f"{district}: still finding listings newer than the watermark "
                f"after {pages} pages; stopping at the page cap",
            )
        return Harvest(caught=caught, complete=complete, pages=pages)


__all__ = [
    "ADDRESS_KEYS", "BASE", "KINDS", "MAX_PAGES", "NEITHER", "NEWEST_FIRST",
    "NOT_A_DWELLING", "PAGE_MODEL", "PAGE_STEP", "PREFER_JPEG", "SOURCE_KEY",
    "Read", "Rightmove", "a_dwelling", "as_listing", "at", "catches_in",
    "kind_of", "monthly", "next_data", "page_model", "picture", "postcode_on",
    "promoted", "results_in", "said_type", "search_url", "type_of", "when",
]
