"""Zoopla, read from its own search pages.

The same shape as Rightmove and it plugs into the same engine
(`worker.sources.sweep`), but almost nothing about the extraction is the same,
so this is a second adapter rather than a parameter on the first.

── where the data are ───────────────────────────────────────────────────────

Zoopla is a Next.js App Router site: there is no `__NEXT_DATA__` block. The
page ships a React Server Components stream instead, as a run of
`self.__next_f.push([1, "<a JS string>"])` calls whose decoded strings
concatenate into one payload. Inside it, `"regularListingsFormatted"` is a
clean JSON array of the listings, one object each, already parsed and typed.

Reading that one array — rather than scanning the payload for anything that
looks like a listing — matters more than it sounds, because the payload holds
two other arrays:

  * `featuredListingsFormatted` — promoted, and out of date order.
  * `extendedListingsFormatted` — listings *outside* the district, from
    Zoopla's widened-radius fallback. Storing these against the searched
    district would send somebody flats in an area they did not choose.

Neither is read. Confirmed on a live E14 page: `regularListingsFormatted` held
25 listings, all in E14, all with `isPremium: false`, and sorted newest first
with no injected rows — which is cleaner than Rightmove's single mixed list.

── the date, and why it is stored as the end of its day ─────────────────────

`publishedOn` is a day, not an instant: "26th Sep 2026". Everything else here
runs on minutes, so the day has to be turned into a comparable moment, and
which end of the day it is matters.

It is stored as the **end** of the published day. The alternative, midnight,
would mean that a district first watched at 14:00 could never announce
anything published that same day — the watch would start after the listing's
timestamp — and a new subscriber would hear nothing until tomorrow. Taking the
end of the day instead means a listing published today counts as later than a
watch begun today, which is right for every listing that actually arrives
after we start looking.

The cost is bounded and worth naming: on the single run that starts watching a
district, a listing published earlier that same day but sitting on page two
may be announced when it was not strictly news. It cannot happen on any later
run, because by then we have seen it and the engine's own record of what it
has stored rules it out.

── what a page costs ────────────────────────────────────────────────────────

About 62KB across the wire for 25 listings — half of Rightmove's 120KB, and
gzip is on by default. `robots.txt`, read on 26 September 2026, permits
`/to-rent/property/…`; the disallowed rental paths are `/to-rent/fees/*`,
`/to-rent/offices/*`, `/to-rent/cottages/*`, `/to-rent/student-accommodation/`
and `/for-rent2/*`, none of which is touched here. No `Crawl-delay`.

Zoopla refuses a plain client outright — it answered 403 even to `robots.txt`
from `urllib` — and serves the same request under impersonation. So it needs
the shared transport for exactly the reason Rightmove does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from pydantic import ValidationError

from worker.contracts.listing import Furnished, Listing
from worker.obs.log import Stage
from worker.sources import postcode
from worker.sources.fetch import Fetcher
from worker.sources.sweep import Catch, Harvest, Memory

SOURCE_KEY = "zoopla"
BASE = "https://www.zoopla.co.uk"

# Newest first. Zoopla's own sort name, and it orders on `publishedOn`:
# confirmed across two pages, where page one was all of the 26th and page two
# all of the 25th.
NEWEST_FIRST = "newest_listings"

# Pages per district per run. A district produces a page of 25 over a day or
# two, so this is only ever reached if something is wrong.
MAX_PAGES = 5

# The array that holds the district's own listings, in order. See the module
# note for the two arrays beside it that must not be read.
LISTINGS_KEY = "regularListingsFormatted"

# Each `self.__next_f.push([1, "…"])` carries one piece of the stream as a
# JavaScript string literal. It is decoded with the JSON parser rather than by
# unescaping it here, because the escaping is JSON's and getting it subtly
# wrong would corrupt listings rather than fail loudly.
FLIGHT = re.compile(
    r'self\.__next_f\.push\(\[\s*\d+\s*,\s*(".*?")\s*\]\)', re.DOTALL
)

# "Balfron Tower, St Leonards Road, London E14" — the outward code closes the
# address. Zoopla stops there and never states the inward half, so unlike
# Rightmove there is no full postcode to be had from a search page.
OUTCODE_AT_END = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*$")

# "26th Sep 2026", "1st Oct 2026", "3rd Jan 2027".
PUBLISHED = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})\s+(\d{4})", re.IGNORECASE
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Zoopla's machine-readable types, in snake_case. Collected from live pages:
# flat, terraced, semi_detached, detached, town_house, maisonette, bungalow,
# and null.
#
# Matched on whole words, against a name whose underscores have been turned
# into spaces first. That normalisation is the whole trick: a regex counts `_`
# as a word character, so `\bdetached\b` does not match "semi_detached" and the
# commonest kind of London house was quietly coming back as no type at all.
# Normalising once beats writing every pattern twice.
KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Before the flat and house patterns: all three names contain one.
    (re.compile(r"\b(?:house|flat|property|maisonette)\s*shares?\b", re.I), "room"),
    (re.compile(r"\brooms?\b", re.I), "room"),
    (re.compile(r"\bstudio", re.I), "flat"),
    (re.compile(r"\bflats?\b", re.I), "flat"),
    (re.compile(r"\bapartment", re.I), "flat"),
    (re.compile(r"\bpenthouse\b", re.I), "flat"),
    (re.compile(r"\bmaisonette\b", re.I), "flat"),
    (re.compile(r"\bduplex\b", re.I), "flat"),
    (re.compile(r"\bbungalow", re.I), "house"),
    (re.compile(r"\bterrac", re.I), "house"),
    (re.compile(r"\bdetached\b", re.I), "house"),
    (re.compile(r"\bcottage", re.I), "house"),
    (re.compile(r"\bmews\b", re.I), "house"),
    (re.compile(r"\bvilla\b", re.I), "house"),
    (re.compile(r"\btown\s*house\b", re.I), "house"),
    (re.compile(r"\bhouses?\b", re.I), "house"),
)

# Somewhere to live, but not one of the four words. See rightmove.NEITHER.
NEITHER = re.compile(
    r"\b(?:boat|barge|houseboat|mobile\s*home|park\s*home|caravan|lodge)\b",
    re.IGNORECASE,
)

# Not somewhere anybody lives. Zoopla's rental channel carries these too.
NOT_A_DWELLING = re.compile(
    r"\b(?:parking|garage|land|plot|commercial|office|retail|industrial|"
    r"warehouse|block\s*of\s*(?:apartments|flats))\b",
    re.IGNORECASE,
)

# "2 bed flat to rent", "Room to rent", "1 bed semi-detached house to rent".
# The bedroom count is in the `features` array as well, and that is what is
# read; this is the fallback for a listing whose features are missing.
BEDS_IN_TITLE = re.compile(r"^(\d+)\s*bed\b", re.IGNORECASE)

FURNISHING: tuple[tuple[Furnished, re.Pattern[str]], ...] = (
    ("part", re.compile(r"\bpart[\s-]*furnished\b", re.IGNORECASE)),
    ("unfurnished", re.compile(r"\bunfurnished\b", re.IGNORECASE)),
    ("furnished", re.compile(r"\bfurnished\b", re.IGNORECASE)),
)

SPACES = re.compile(r"\s+")

PETS = re.compile(r"\bpets?\s*(?:allowed|welcome|considered|friendly)\b", re.IGNORECASE)
BILLS = re.compile(r"\bbills?\s*(?:are\s*)?includ", re.IGNORECASE)

# The rent, read from the string Zoopla displays — "£3,831 pcm" — because the
# number beside it cannot be trusted.
#
# `priceUnformatted` is whatever unit the agent typed. On a live E14 page, one
# listing showed `price: "£3,831 pcm"` with `priceUnformatted: 884`, the weekly
# figure, while its neighbour showed `"£3,841 pcm"` with `priceUnformatted:
# 3841`. Taking the number would have stored that flat at £884 and sent it to
# everybody with a small budget, with the wrong rent printed in the alert.
#
# So the displayed string is authoritative: it carries the amount and the unit
# together and the two always agree. `priceUnformatted` is a fallback for a
# listing whose string cannot be read at all, and then only when the separate
# weekly label does not contradict it.
PRICE_SHOWN = re.compile(
    r"£\s*([\d,]+(?:\.\d+)?)\s*(pcm|pw|pa|per\s+month|per\s+week|per\s+annum)?",
    re.IGNORECASE,
)
PER_WEEK = re.compile(r"\bp\.?w\b|\bper\s+week\b|\bpcw\b", re.IGNORECASE)
PER_MONTH = re.compile(r"\bpcm\b|\bp\.?c\.?m\b|\bper\s+month\b", re.IGNORECASE)


def search_url(district: str, page: int = 1) -> str:
    """The search page for one outcode, newest first."""

    url = f"{BASE}/to-rent/property/{district.lower()}/?results_sort={NEWEST_FIRST}"
    return url if page <= 1 else f"{url}&pn={page}"


def flight_stream(page: str) -> str:
    """The RSC payload, reassembled from the page's push calls."""

    pieces: list[str] = []
    for found in FLIGHT.finditer(page):
        try:
            pieces.append(json.loads(found.group(1)))
        except json.JSONDecodeError:
            # One unreadable chunk is not worth losing the page over; the
            # array we want may well be whole in the rest.
            continue
    return "".join(pieces)


def _array_after(stream: str, key: str) -> list[Any]:
    """The JSON array that `key` introduces, found by balancing brackets.

    The stream is not a JSON document — it is a concatenation of framed
    chunks — so it cannot simply be parsed. Bracket balancing from the key is
    what makes one array inside it readable without trying to understand the
    framing.
    """

    at = stream.find(f'"{key}":')
    if at < 0:
        return []
    start = stream.find("[", at)
    if start < 0:
        return []
    depth = 0
    inside_string = False
    escaped = False
    for here in range(start, len(stream)):
        char = stream[here]
        if inside_string:
            # Brackets inside a string — an address, a caption — must not be
            # counted, and a quote after a backslash does not end the string.
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside_string = False
            continue
        if char == '"':
            inside_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stream[start : here + 1])
                except json.JSONDecodeError:
                    return []
                return parsed if isinstance(parsed, list) else []
    return []


def listings_in(page: str) -> list[Any]:
    """The district's own listings, in Zoopla's order."""

    return _array_after(flight_stream(page), LISTINGS_KEY)


def pages_total(page: str) -> int | None:
    """How many pages this search has, from Zoopla's own pagination."""

    found = re.search(r'"pageNumberMax":\s*(\d+)', flight_stream(page))
    return int(found.group(1)) if found else None


def feature(row: dict[str, Any], icon: str) -> int | None:
    """One of the little bed/bath/area figures under a search result.

    They arrive as `{"content": 2, "iconId": "bed"}`, which is where the
    bedroom and bathroom counts live — there is no `bedrooms` field on these
    objects at all.
    """

    listed = row.get("features")
    if not isinstance(listed, list):
        return None
    for one in listed:
        if isinstance(one, dict) and str(one.get("iconId") or "") == icon:
            try:
                return int(one.get("content"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
    return None


def published(raw: Any) -> datetime | None:
    """"26th Sep 2026" as the *end* of that day. See the module note."""

    if not isinstance(raw, str):
        return None
    found = PUBLISHED.search(raw)
    if not found:
        return None
    month = MONTHS.get(found.group(2)[:3].lower())
    if month is None:
        return None
    try:
        day = date(int(found.group(3)), month, int(found.group(1)))
    except ValueError:
        return None
    return datetime.combine(day, time.max, tzinfo=UTC)


def when(raw: Any) -> date | None:
    """A plain date, for "available from"."""

    stamp = published(raw)
    return None if stamp is None else stamp.date()


def monthly(row: dict[str, Any]) -> int | None:
    """The rent per calendar month, or None when it cannot be established.

    Read from the displayed string rather than from `priceUnformatted`. See
    PRICE_SHOWN for the listing that made that necessary.
    """

    shown = str(row.get("price") or "")
    stated = PRICE_SHOWN.search(shown)
    amount: float | None = None
    unit = ""

    if stated:
        try:
            amount = float(stated.group(1).replace(",", ""))
        except ValueError:
            amount = None
        unit = (stated.group(2) or "").lower()

    if amount is None:
        # No readable string. The bare number is worth using only if nothing
        # contradicts it, and a weekly label beside it is a contradiction.
        weekly_label = str(row.get("alternativeRentFrequencyLabel") or "")
        try:
            amount = float(row.get("priceUnformatted"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if PER_WEEK.search(weekly_label):
            label = PRICE_SHOWN.search(weekly_label)
            try:
                weekly = float(label.group(1).replace(",", "")) if label else None
            except ValueError:
                weekly = None
            # The weekly label repeating our own number means the number is
            # the weekly one, and monthly is not knowable from here.
            if weekly is not None and abs(weekly - amount) < 0.01:
                return None

    if PER_WEEK.search(unit):
        amount = amount * 52 / 12
    elif "annum" in unit or unit == "pa":
        amount = amount / 12
    elif unit and not PER_MONTH.search(unit):
        # A unit we do not recognise is refused rather than assumed monthly.
        return None

    rounded = round(amount)
    return rounded if 100 <= rounded <= 100_000 else None


def _words(property_type: Any) -> str:
    """Zoopla's snake_case name as words, so boundaries mean something."""

    return str(property_type or "").replace("_", " ")


def kind_of(property_type: Any) -> str | None:
    words = _words(property_type)
    if NEITHER.search(words):
        return None
    for pattern, kind in KINDS:
        if pattern.search(words):
            return kind
    return None


def a_dwelling(property_type: Any) -> bool:
    return not NOT_A_DWELLING.search(_words(property_type))


def said_type(row: dict[str, Any]) -> tuple[str, ...]:
    """What this card says the property is, most authoritative first.

    `propertyType` leads, and behind it the two places Zoopla puts the type
    when that field is null — which it is on about one listing in sixteen.
    Measured over 200 live listings across eight districts on 2 October 2026:
    twelve had no `propertyType`, and seven of those twelve were rooms, saying
    so in `title` ("Room to rent") and in `tags` ("House share"). The other
    five said "3 bed property to rent" and nothing else, which is Zoopla
    genuinely not stating it; those stay untyped, honestly.

    Why it matters more than one in sixteen sounds. `match._check_property_type`
    passes a listing whose type is unknown — silence never excludes, which is
    right — so an untyped listing reaches everybody. The untyped ones here are
    mostly house shares, so somebody who asked for a flat was being sent rooms,
    and the filter looked broken when the data was simply in another field.

    ── why a tuple and not one joined string ────────────────────────────────

    Because joining them loses the order, and the order is the whole point.
    `KINDS` is matched room-first, so that "house share" is a room rather than
    a house — and against one joined string a card reading `propertyType:
    detached_house` with "Room to rent" in its title came back a room. Each
    source is therefore read on its own, in turn, and the first to name a type
    wins.
    """

    tags = " ".join(
        str(tag.get("content") or "")
        for tag in (row.get("tags") or [])
        if isinstance(tag, dict)
    )
    return tuple(
        part
        for part in (_words(row.get("propertyType")), str(row.get("title") or ""), tags)
        if part.strip()
    )


def type_of(row: dict[str, Any]) -> str | None:
    """One of the four words the filter offers, from wherever the card says it."""

    for words in said_type(row):
        kind = kind_of(words)
        if kind is not None:
            return kind
    return None


def card_is_a_dwelling(row: dict[str, Any]) -> bool:
    """Whether this card is somewhere to live, judged on what it actually says.

    The most authoritative source and that one only: `propertyType` where there
    is one, the title where there is not. Checking the title as well as the
    field would start reading "near the parking garage" in an agent's prose as
    a parking space, and dropping a real flat is a worse mistake than storing a
    garage nobody's filter will match.
    """

    said = said_type(row)
    return a_dwelling(said[0]) if said else True


def picture(row: dict[str, Any]) -> str | None:
    """The smallest published variant of the preview photograph.

    Zoopla offers the same picture at 645 and at 354 wide. The narrow one is
    what an alert shows, and it is the subscriber's phone that pulls it, so the
    smaller url is the one worth storing. Nothing is downloaded here — see the
    note in worker.sources.sweep.Catch.
    """

    image = row.get("image")
    if not isinstance(image, dict):
        return None

    listed = image.get("responsiveImgList")
    best: tuple[int, str] | None = None
    if isinstance(listed, list):
        for one in listed:
            if not isinstance(one, dict):
                continue
            src = str(one.get("src") or "").strip()
            if not src.startswith("https://"):
                continue
            try:
                width = int(one.get("width"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if best is None or width < best[0]:
                best = (width, src[:1000])
    if best is not None:
        return best[1]

    fallback = str(image.get("src") or "").strip()
    return fallback[:1000] if fallback.startswith("https://") else None


def prose(row: dict[str, Any]) -> str:
    """The listing's own words, for the facts only text states."""

    parts = [str(row.get("title") or ""), str(row.get("summaryDescription") or "")]
    tags = row.get("tags")
    if isinstance(tags, list):
        parts.extend(
            str(one.get("content") or "") for one in tags if isinstance(one, dict)
        )
    return " · ".join(part for part in parts if part)


def as_listing(row: Any, district: str) -> Listing | None:
    """One search result as a `Listing`, or nothing if it is unusable."""

    if not isinstance(row, dict):
        return None
    # Judged on what the card says rather than on one field, because that
    # field is null often enough to matter — see `said_type`.
    if not card_is_a_dwelling(row):
        return None

    listing_id = str(row.get("listingId") or "").strip()
    price = monthly(row)
    if not listing_id or price is None:
        return None

    uris = row.get("listingUris")
    path = ""
    if isinstance(uris, dict):
        path = str(uris.get("detail") or "")
    url = f"{BASE}{path}" if path.startswith("/") else f"{BASE}/to-rent/details/{listing_id}/"

    beds = feature(row, "bed")
    if beds is None:
        # "2 bed flat to rent". A room in a shared house has no count at all
        # and its title simply reads "Room to rent", which is a studio's zero
        # as far as this project is concerned — see the contract.
        in_title = BEDS_IN_TITLE.search(str(row.get("title") or ""))
        beds = int(in_title.group(1)) if in_title else 0

    words = prose(row)
    furnished: Furnished = "unknown"
    for name, pattern in FURNISHING:
        if pattern.search(words):
            furnished = name
            break

    # Collapsed rather than stripped, for the same reason as Rightmove's: an
    # alert is a list of one-line facts and a portal is free to embed a
    # newline in an address.
    address = SPACES.sub(" ", str(row.get("address") or "")).strip()
    where = row.get("pos")
    where = where if isinstance(where, dict) else {}

    # Theirs is in square feet already — `sizeSource` said `structured_data`,
    # and the little `area` figure under a result agrees with it.
    area = feature(row, "area")
    if area is None:
        try:
            area = int(row.get("sizeSqft"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            area = None

    return Listing(
        source_key=SOURCE_KEY,
        external_id=listing_id,
        url=url,
        price_pcm=price,
        bedrooms=beds,
        bathrooms=feature(row, "bath"),
        property_type=type_of(row),
        furnished=furnished,
        pets_allowed=True if PETS.search(words) else None,
        bills_included=True if BILLS.search(words) else None,
        available_from=when(row.get("availableFrom")),
        min_tenancy_months=None,
        deposit_pcm=None,
        # Zoopla states the outward code and stops. There is no inward half to
        # be had from a search page, and half a postcode is worse than none:
        # the duplicate rule compares full postcodes, and an outward code alone
        # would make every 2-bed at one price in E14 the same flat.
        postcode=None,
        postcode_district=district.upper(),
        lat=_coord(where.get("lat"), 90),
        lng=_coord(where.get("lng"), 180),
        title=address or str(row.get("title") or "") or None,
        description=str(row.get("summaryDescription") or "") or None,
        # A branch means an agent. Zoopla does carry some direct lettings, so
        # the absence of one is left unknown rather than called a landlord.
        is_landlord_direct=False if isinstance(row.get("branch"), dict) else None,
        photo_count=_count(row.get("numberOfImages")),
        floor_area_sqft=area if area and 50 <= area <= 20_000 else None,
        raw={
            "property_type": str(row.get("propertyType") or ""),
            "published_on": str(row.get("publishedOn") or ""),
            "address": address,
            "outcode_in_address": stated_outcode(address) or "",
        },
    )


def stated_outcode(address: str) -> str | None:
    """The outward code the address itself ends with, if it states one."""

    found = OUTCODE_AT_END.search(address.upper().strip())
    return found.group(1) if found else None


def _count(raw: Any) -> int | None:
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coord(raw: Any, limit: float) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if -limit <= value <= limit else None


@dataclass(frozen=True)
class Read:
    """One search page, as far as this module is concerned."""

    caught: list[Catch]
    total_pages: int | None = None
    skipped: int = 0
    invalid: int = 0


def catches_in(page: str, district: str) -> Read:
    """Every usable listing on one Zoopla search page."""

    rows = listings_in(page)
    caught: list[Catch] = []
    invalid = 0
    for row in rows:
        try:
            listing = as_listing(row, district)
        except ValidationError:
            # Never fatal — see the same guard in openrent and rightmove.
            invalid += 1
            continue
        if listing is None:
            continue
        caught.append(
            Catch(
                listing=listing,
                image=picture(row) if isinstance(row, dict) else None,
                first_listed=published(
                    row.get("publishedOn") if isinstance(row, dict) else None
                ),
            )
        )
    return Read(
        caught=caught,
        total_pages=pages_total(page),
        skipped=len(rows) - len(caught) - invalid,
        invalid=invalid,
    )


@dataclass(frozen=True)
class Zoopla:
    """The portal, as `worker.sources.sweep.collect` needs it."""

    key: str = SOURCE_KEY
    #: `publishedOn` is a day rather than an instant, but it is a date the
    #: portal stands behind, so announceability is decided from it. See the
    #: module note on why it is stored as the end of its day.
    dated: bool = True
    max_pages: int = MAX_PAGES

    def postcode_for(
        self, catch: Catch, get: Fetcher, stage: Stage
    ) -> str | None:
        """The full postcode, from the listing's own page.

        Zoopla states none at all on a search page — every address ends at the
        outward code — so without this every Zoopla listing went out saying
        only "E14", and `store.mark_duplicate` could never recognise one as
        the same flat Rightmove had already sent.

        Its page carries the halves as structured fields, `"outcode": "E14"`
        and `"incode": "0UY"`, so there is nothing to guess. About 47KB.
        """

        del stage
        reply = get.get(catch.listing.url)
        return postcode.from_fields(reply.body, catch.listing.postcode_district)

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
        page_number = 1

        while pages < self.max_pages:
            reply = get.get(search_url(district, page_number))
            pages += 1
            read = catches_in(reply.body, district)
            if read.skipped:
                stage.count("not_a_listing", read.skipped)
            if read.invalid:
                stage.count("invalid", read.invalid)

            if not read.caught:
                # An empty district, or a payload shape we did not recognise.
                # Either way there is nothing beyond this page.
                complete = True
                break

            for one in read.caught:
                if one.listing.external_id not in seen:
                    seen.add(one.listing.external_id)
                    caught.append(one)

            if since is None:
                # Starting to watch: one page, and nothing on it will be
                # announced. See worker.sources.sweep.
                complete = True
                break

            # Stop once the page reaches a day before the watch began. No
            # promoted-row exception is needed here: the promoted listings are
            # in a separate array that this module never reads, so what comes
            # back is in date order throughout.
            if any(
                one.sort_key is None or one.sort_key <= since for one in read.caught
            ):
                complete = True
                break

            if read.total_pages is not None and page_number >= read.total_pages:
                complete = True
                break
            page_number += 1

        if not complete:
            stage.log(
                "warn",
                f"{district}: still finding listings published since the watch "
                f"after {pages} pages; stopping at the page cap",
            )
        return Harvest(caught=caught, complete=complete, pages=pages)


__all__ = [
    "BASE", "KINDS", "LISTINGS_KEY", "MAX_PAGES", "NEITHER", "NEWEST_FIRST",
    "NOT_A_DWELLING", "SOURCE_KEY", "Read", "Zoopla", "a_dwelling",
    "as_listing", "card_is_a_dwelling", "catches_in", "feature",
    "flight_stream", "kind_of", "listings_in", "monthly", "pages_total",
    "picture", "prose", "published", "said_type", "search_url",
    "stated_outcode", "type_of", "when",
]
