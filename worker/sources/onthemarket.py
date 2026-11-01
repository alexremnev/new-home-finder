"""OnTheMarket, read as batches of districts over the site's own 24-hour window.

The fourth scraped portal and the cheapest of the four, for one reason: its
search accepts up to nine locations in a single query, so the whole of London
is read in fourteen requests rather than one per outcode. Everything below was
measured against the live site on 31 October and 1 November 2026 and is
load-bearing; none of it is guessable from the code.

── why there is no newest-first read here ───────────────────────────────────

`robots.txt`, read on 1 November 2026, disallows for `User-agent: *`:

    Disallow: *sort-field=**     Disallow: *direction=
    Disallow: *exclusive-first=  Disallow: *let-agreed=**
    Disallow: *min-price=  *max-price=  *min-bedrooms=  *max-bedrooms=
    Disallow: *prop-types=  *radius=  *bounding-box=  *view=map*

So `?sort-field=update_date` — the "Recent" option in the site's own sort
menu — is out of bounds, exactly as `sort_by=days_since_placed` is on
SpareRoom. And it would not have helped: asked for it once, the first result
on the London page was labelled "Added > 14 days", because `exclusive-first`
and `spotlight` lift paid slots above the ordering and the field is the
*update* date, so a re-advertised flat from July outranks one from this
morning. Turning either off needs a parameter robots.txt forbids.

Reading the city unfiltered is not an option either: `/to-rent/property/london/`
reported 35,822 rentals against a `limit-results.properties` ceiling of 1,000
in the page's own globals, so 34,822 of them are unreachable by any paging.

── what is read instead, and why it needs no watermark ─────────────────────

`recently-added=24-hours` is NOT in the disallow list, and it is the whole
design. It answers with everything added or price-reduced in the last rolling
24 hours — 647 across London, 248 across the 101 districts of zones 1-3, and
one or two pages per district.

That makes this the only reader here whose *query* is its completeness
guarantee. There is no watermark to reach, no ordering to trust and no page
cap to stop early at: a run either read the window or it did not. A missed run
costs nothing, because the next one reads the same 24 hours — and an outage
longer than the window is exactly the case `sweep.GAP_HOURS` already voids the
watch for. `since` is therefore accepted and ignored, which is the one place
this reader differs from every other `Portal` here.

── the nine-location query ──────────────────────────────────────────────────

The parameter repeats; it is not a comma-separated list. Measured both ways:

    ?alt-location-ids=se8&alt-location-ids=e14      altLocationIds: [se8, e14]
    ?alt-location-ids=se8,se14                      altLocationIds: []

The ceiling is **nine locations** — one in the path plus eight in the query —
and it is a count and not a url length. Nine long area names at 270 characters
were served; ten short outcodes at 225 were not.

Checked against reading the same districts one at a time, by comparing the sets
of ids rather than the counters:

    nine outcodes, one query each     63 listings, 9 requests, 513KB
    the same nine in one query        63 listings, 3 requests, 209KB

Zero in either difference. The batch also folds away the duplicate a listing on
a district boundary produces when two neighbouring outcodes are read separately.

── the tenth location fails SILENTLY, which is why `_honoured` exists ──────

Asked for ten, the site answers **200** with `totalResults: null`, an empty
list and `useMultilocation: false`. Not a 400, not a redirect — an ordinary
empty results page. A reader that trusted it would report "nothing new in ten
districts" on every run, for ever, and look healthy doing it.

`BATCH` makes that unreachable by construction, so the guard is not really
about the count: it is about the day the parameter is renamed. The same
applies to the recency filter, which would silently widen the read to the whole
district if it were dropped. Both are therefore re-checked against
`query.altLocationIds` and `query.recentlyAdded` on **every** page, not just
the first — paging is where a parameter gets lost.

── the 404 that is not an answer ────────────────────────────────────────────

The origin sheds load as `404 Not Found` from nginx, with
`x-cache: Error from cloudfront` and no body. On deep offsets of the
whole-London query it is routine: 5 of 12 pages served on first attempt, and
retries at +3s and +8s did not clear it, while 20 seconds between requests gave
6 of 6. Reading in batches avoids the conditions entirely — every batch page is
page 1 to 3 of a small result set, and a full zone 1-3 sweep took 14 requests
with zero sheds — but "rare" is not "never", and a shed page silently drops a
district's whole window.

So a 404 here is retried, directly, after a pause, and `tolerate=(404,)` keeps
`Fetcher.get` from spending a second fingerprint or a proxy exit on it. That
matters: `fetch.BLOCKED` deliberately excludes 404 from the rotation, but the
reader still has to not treat the refusal as a block. Paying residential
traffic to be told 404 again is the one expensive mistake available here.

── what the cards do and do not say ─────────────────────────────────────────

89 cards across three batches, 1 November 2026. Everything the filters ask
about except four fields is on the card, free:

    price, bedrooms, bathrooms, humanised-property-type, address,
    location.lat/lon, cover-image (a 480x320 CDN variant), features, reduced?

`days-since-added-reduced` takes exactly four values inside this window —
"Added today" (63), "Added yesterday" (16), "Reduced yesterday" (6),
"Reduced today" (4) — so the date is a day and is stored as the END of that
day, the treatment `zoopla.published` and `spareroom.first_listed` both give a
date that is only a date, and for the same reason.

A **reduced** card gets no date at all. The label says when the price moved,
not when the flat appeared, and a flat reduced today may have been advertised
since July. `_announceable` refuses a dated portal's listing with no date, so
these are stored and never announced — which is the point. The cost is honest
and bounded: a flat added and reduced inside the same 24 hours shows only the
reduction and is therefore never announced. Rare, and the safe direction.

Four filters are not on a card and come from the listing's own page through
`enrich`, at 58KB each: furnishing, availability date, minimum term and floor
area. Pets too, from `iconLabels`.

── the eight per cent with no district on the card ─────────────────────────

Of 89 cards, 75 ended in an outward code and 7 in a full postcode; 7 ended in
neither — "Commercial Road", "Iverson Road". A listing the engine cannot file
under a district is dead weight: `_enrich` and `_fill_postcode` are both gated
on `postcode_district`, and `_derive_postcodes` passes it to `geo.nearest` as
the check that stops a neighbour's postcode being printed as fact.

Coordinates are on every card, so `geo.nearest` could answer for nothing — but
only by skipping that check, and a fifth of London points have more than one
outward code within 250m. So the district is read from the listing's own page
instead, where `headerData.dataLayer.postcode` stated a full postcode on 14 of
14 pages read. One request, and never twice for the same listing:
`memory.resolved` and `memory.remember` are exactly the record 0053 added for
OpenRent's radius search, and this is the same question.

── what a run costs ─────────────────────────────────────────────────────────

Per batch page, 52-74KB across the wire (gzip; 456-630KB of HTML). Measured:

    3 subscribed districts      1 request       ~70KB a run
    101 districts, zones 1-3    14 requests      954KB a run, 18s wall clock

── the thirty-minute cache this cannot get around ──────────────────────────

Search pages carry `cache-control: public, max-age=1800` and CloudFront honours
it to the letter: the same url returned `age: 448`, then 452, then 455 over a
minute, and `Cache-Control: no-cache`, `Pragma: no-cache` and
`max-age=0` were all ignored. A batch url nobody else requests is therefore
refreshed when our own previous copy expires, which is to say once every thirty
minutes however often the timer fires.

So the latency floor is the TTL and not the schedule, and most runs on a
six-minute timer re-read a byte-identical page. That is a deliberate choice
recorded in the timer unit, not an oversight here. It is also the thing to
remember before blaming this reader for being slower than `rightmove`: against
OnTheMarket's own product — agents listing here a day before the other portals
— fifteen minutes of CDN staleness is not the number that matters.

── this is an agents-only portal, so most of what it finds is a duplicate ──

There is no private-landlord channel, and almost everything here is syndicated
to Rightmove and Zoopla as well. `store.mark_duplicate` compares on the full
postcode, which is why `enrich` reading it is not optional for this source: a
reader that stored these without one would send a second copy of a flat a
subscriber had already been told about. The value of the source is the part
that is NOT syndicated yet — `exclusive` on the listing page — and that is
kept in `raw` so the question can be answered from the database later.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as time_of_day
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worker.contracts.listing import Furnished, Listing
from worker.obs.log import Stage
from worker.sources.fetch import Fetcher, Refused
from worker.sources.sweep import Catch, Harvest, Memory
from worker.units import sqft_from

SOURCE_KEY = "onthemarket"
BASE = "https://www.onthemarket.com"

# The one filter this reader uses, and the only recency the site offers below
# three days. Re-checked on every page: see `_honoured`.
RECENT = "24-hours"

# Locations per query — one in the path, eight in `alt-location-ids`. Measured,
# and a count rather than a url length; see the module note. Asking for more
# returns an empty page with no error at all.
BATCH = 9

# Cards per page, from `globals.frame-size.properties`. Not settable: asked for
# `frame-size=100` and the page came back with no results block at all.
PAGE_SIZE = 30

# Pages per batch per run. A ceiling against a portal bug, not a target: the
# busiest batch of nine central outcodes needed three, and the whole of zones
# 1-3 averaged 1.2. At `PAGE_SIZE` this is 240 listings in nine districts in
# 24 hours, which London does not produce.
MAX_PAGES = 8

# Seconds to wait before asking again for a page the origin shed as 404, and
# how much of that a single run may spend in total.
#
# Twenty because that is what was measured to clear it — three and eight second
# retries did not, twenty-second spacing gave six pages out of six. Sixty
# because a run that has waited a minute for shed pages is a run that should
# report an incomplete read and let the next one try, rather than hold a
# systemd oneshot open against a timer that has already fired behind it.
SHED_PAUSE = 20.0
SHED_BUDGET = 60.0

# Listing pages per run for `enrich`. Four filters and the full postcode come
# off this page and nothing else does, so the figure is the number of new
# listings in subscribed districts worth a 58KB page — about 20 a day in a busy
# central outcode, and the ceiling is here so that a parse that suddenly thinks
# every listing is new cannot turn one run into two hundred requests.
DETAIL_BUDGET = 40

# Listing pages per run spent only to learn which district a listing is in,
# for the cards whose address ends in neither an outward code nor a postcode.
# Separate from `DETAIL_BUDGET` because it is spent BEFORE the engine knows
# whether anybody subscribes to the district, so it is the one detail traffic
# this reader cannot gate on `wanted`. Eight per cent of cards, and each one is
# asked about once ever — see `memory`.
RESOLVE_BUDGET = 15

NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

SPACES = re.compile(r"\s+")

# "Biscuit Walk, Bermondsey, London, SE16" and
# "Balfron Tower, 7 St Leonards Road, London E14 0UY" — the site states either,
# and the full form on about one card in twelve.
FULL_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s+(\d[A-Z]{2})\s*$")
OUTCODE_TAIL = re.compile(r"\b([A-Z]{1,2}\d{1,2}[A-Z]?)\s*$")
OUTCODE = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?$")

# "£2,445 pcm (£564 pw)". Every card in the sample stated pcm first; the weekly
# figure is the site's own conversion of it and is not read.
PCM = re.compile(r"£\s*([\d,]+)\s*pcm", re.IGNORECASE)
PER_WEEK = re.compile(r"£\s*([\d,]+)\s*pw", re.IGNORECASE)

MONEY = re.compile(r"£\s*([\d,]+(?:\.\d{1,2})?)")

# "Added today", "Added yesterday", "Reduced today", "Reduced yesterday" — the
# complete set inside a 24-hour window, counted across 89 cards. Anything else
# means the recency filter was not applied, which `_honoured` catches first.
ADDED_TODAY = re.compile(r"^added\s+today$", re.IGNORECASE)
ADDED_YESTERDAY = re.compile(r"^added\s+yesterday$", re.IGNORECASE)

# `lettingDetails.items`, by frequency across 34 listing pages. The first entry
# is the trap: "Available as furnished or unfurnished" is the most common item
# of all and starts with the word a naive availability parser looks for.
FURNISHING: tuple[tuple[Furnished, re.Pattern[str]], ...] = (
    # Checked before the two below, because it contains both their words.
    ("unknown", re.compile(r"furnished\s+or\s+unfurnished", re.IGNORECASE)),
    ("part", re.compile(r"\bpart(?:ly|ially)?[\s-]*furnished\b", re.IGNORECASE)),
    ("unfurnished", re.compile(r"\bunfurnished\b", re.IGNORECASE)),
    ("furnished", re.compile(r"\bfurnished\b", re.IGNORECASE)),
)

AVAILABLE_NOW = re.compile(r"^available\s+now$", re.IGNORECASE)
AVAILABILITY = re.compile(r"^availability\s+date:\s*(.+)$", re.IGNORECASE)
# "Min. term: 12 months" is the minimum; "Contract length: 12 months" is what
# the agent is offering. Both are read, the first wins where both appear.
MIN_TERM = re.compile(r"^min\.?\s*term:\s*(\d+)\s*month", re.IGNORECASE)
CONTRACT = re.compile(r"^contract\s+length:\s*(\d+)\s*month", re.IGNORECASE)
DEPOSIT = re.compile(r"^deposit:\s*(.+)$", re.IGNORECASE)

# "29 Nov 2026". The only availability format seen on a live page.
AVAILABLE_ON = "%d %b %Y"

# The latest moment of a day, for a portal that publishes only the day. Named
# rather than inlined because `datetime.time` and the `time` module share a
# word and the two are a line apart below.
LAST_MOMENT = time_of_day.max

# Whose "today" the site means. The server runs on UTC and the site is writing
# for London, so this is not decoration: through British Summer Time the hour
# after midnight in London is the previous day in UTC, and a flat posted at
# 00:30 on a July morning would be filed as yesterday's. For a district whose
# watch settled during that day, `_announceable` then refuses it — the one
# listing an hour-wide seam a day would quietly lose.
#
# `spareroom.first_listed` takes the machine's date and has the same seam; it
# is not changed here because its window rotates and re-reads what it missed,
# which this reader's fixed window does not do for a listing it has already
# stored under the wrong day.
LONDON = ZoneInfo("Europe/London")


def today_in_london() -> date:
    """The date it is where the site is writing from."""

    return datetime.now(LONDON).date()

# `iconLabels[].title`. Three were seen — "Pet friendly", "Greener choice" and
# "Accessible features" — and only the first answers a filter.
PET_FRIENDLY = "pet friendly"

# `humanised-property-type`, every value across 89 cards:
#   Flat 52, Apartment 18, Studio 11, Penthouse 2, Maisonette 2,
#   House share 1, Bedsit 1, Flat share 1, Detached house 1
#
# Narrowed to the three words `criteria.PROPERTY_TYPES` offers. Order matters:
# "House share" and "Flat share" are rooms and both contain a word that is not
# what they are, so the share forms are tested first.
ROOM = re.compile(r"\b(?:house|flat|property)\s*share\b|\bbedsit\b", re.IGNORECASE)
FLAT = re.compile(
    r"\b(?:flat|apartment|studio|penthouse|maisonette|duplex)\b", re.IGNORECASE
)
HOUSE = re.compile(r"\b(?:house|bungalow|cottage|town\s*house|villa)\b", re.IGNORECASE)

# Not somewhere anybody lives. Rightmove's rental channel carries parking
# spaces and this one carries garages and land; left in, a £250 garage answers
# a studio search on rent and bedroom count alike.
NOT_A_DWELLING = re.compile(
    r"\b(?:parking|garage|land|plot|commercial|office|retail|boat)\b", re.IGNORECASE
)


def search_url(districts: tuple[str, ...], page: int = 1) -> str:
    """The search for one batch of districts, newest window first.

    The first district is the path and the rest are `alt-location-ids`, which
    is the site's own arrangement rather than a choice — there is no url shape
    that names nine locations symmetrically.

    `page=1` is the bare url and never `?page=1`, which answers 404.
    """

    if not districts:
        raise ValueError("a batch needs at least one district")
    if len(districts) > BATCH:
        # Unreachable through `plan`, and worth failing loudly rather than
        # sending a query the site answers with a silent empty page.
        raise ValueError(
            f"{len(districts)} locations asked for; OnTheMarket serves at most {BATCH}"
        )
    head, *rest = (one.strip().lower() for one in districts)
    query = [f"alt-location-ids={one}" for one in rest]
    query.append(f"recently-added={RECENT}")
    if page > 1:
        query.append(f"page={page}")
    return f"{BASE}/to-rent/property/{head}/?" + "&".join(query)


def listing_url(external_id: str) -> str:
    """The listing's own page. One shape across 89 cards: `/details/<id>/`."""

    return f"{BASE}/details/{external_id}/"


def next_data(page: str) -> dict[str, Any]:
    """The embedded payload, or an empty dict when the page carries none."""

    found = NEXT_DATA.search(page)
    if not found:
        return {}
    try:
        parsed = json.loads(found.group(1))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _state(page: str) -> dict[str, Any]:
    state = (
        next_data(page).get("props", {}).get("pageProps", {}).get("initialReduxState")
    )
    return state if isinstance(state, dict) else {}


def results_in(page: str) -> dict[str, Any]:
    """`initialReduxState.results` — the cards, the count and the pagination."""

    found = _state(page).get("results")
    return found if isinstance(found, dict) else {}


def query_in(page: str) -> dict[str, Any]:
    """`initialReduxState.query` — what the site believes it was asked."""

    found = _state(page).get("query")
    return found if isinstance(found, dict) else {}


def honoured(page: str, districts: tuple[str, ...]) -> str | None:
    """Why this page cannot be trusted, or None when it can.

    Both halves of the query are re-checked, on every page. See the module
    note: the failure this guards is a 200 with an empty list, which is
    indistinguishable from a quiet district unless the parameters are read
    back.
    """

    query = query_in(page)
    if not query:
        return "the page carried no query state"
    said = query.get("recentlyAdded")
    if said != RECENT:
        return (
            f"the recency filter came back as {said!r} rather than {RECENT!r}, "
            f"so this is the whole district and not its last 24 hours"
        )
    if len(districts) == 1:
        return None
    asked = [one.strip().lower() for one in districts[1:]]
    got = query.get("altLocationIds")
    got = [str(one).strip().lower() for one in got] if isinstance(got, list) else []
    if sorted(got) != sorted(asked):
        return (
            f"asked for {len(asked) + 1} locations and the page came back with "
            f"{len(got) + 1}; `alt-location-ids` was not honoured"
        )
    return None


def next_page(page: str) -> bool:
    """Whether the site says there is another page of this search."""

    nxt = results_in(page).get("paginationControls")
    if not isinstance(nxt, dict):
        return False
    return bool((nxt.get("next") or {}).get("next-link"))


def monthly(price: Any) -> int | None:
    """"£2,445 pcm (£564 pw)" as 2445.

    The weekly figure is a fallback and not a preference: it is the site's own
    rounding of the monthly one, so converting it back lands a few pounds out.
    """

    text = str(price or "")
    found = PCM.search(text)
    if found:
        try:
            return int(found.group(1).replace(",", ""))
        except ValueError:
            return None
    weekly = PER_WEEK.search(text)
    if not weekly:
        return None
    try:
        return round(int(weekly.group(1).replace(",", "")) * 52 / 12)
    except ValueError:
        return None


def kind_of(humanised: Any) -> str | None:
    """One of flat, house or room, or None for anything else.

    None rather than a guess: an unstated type matches every filter, which is
    the direction that hides nothing. The portal's own word is kept in `raw`.
    """

    text = str(humanised or "")
    if not text.strip():
        return None
    if ROOM.search(text):
        return "room"
    if FLAT.search(text):
        return "flat"
    if HOUSE.search(text):
        return "house"
    return None


def a_dwelling(humanised: Any) -> bool:
    """Whether this is somewhere a person lives."""

    return not NOT_A_DWELLING.search(str(humanised or ""))


def first_listed(label: Any, *, reduced: bool, today: date | None = None) -> datetime | None:
    """When the flat appeared, from `days-since-added-reduced`.

    None for a reduced card and for any label this has not learned: both mean
    the page is not telling us when the flat appeared, and `_announceable`
    stores those without announcing them.

    Stored as the END of the day named, for the reason `zoopla.published`
    gives: a district first watched at 14:00 could otherwise never announce
    anything added that same day.
    """

    if reduced:
        return None
    text = str(label or "").strip()
    now = today or today_in_london()
    if ADDED_TODAY.match(text):
        return datetime.combine(now, LAST_MOMENT, tzinfo=UTC)
    if ADDED_YESTERDAY.match(text):
        return datetime.combine(now - timedelta(days=1), LAST_MOMENT, tzinfo=UTC)
    return None


def where_in(address: str) -> tuple[str | None, str | None]:
    """(full postcode, outward code) from a card's address.

    Both may be None — 7 of 89 cards ended in neither, which is what
    `OnTheMarket._district_of` exists for.
    """

    tidy = SPACES.sub(" ", address or "").strip().upper()
    full = FULL_POSTCODE.search(tidy)
    if full:
        return f"{full.group(1)} {full.group(2)}", full.group(1)
    outward = OUTCODE_TAIL.search(tidy)
    return None, outward.group(1) if outward else None


def picture(card: dict[str, Any]) -> str | None:
    """The pre-resized 480x320 variant the card already carries."""

    cover = card.get("cover-image")
    if isinstance(cover, dict):
        for name in ("default", "webp"):
            url = cover.get(name)
            if isinstance(url, str) and url.startswith("http"):
                return url
    images = card.get("images")
    if isinstance(images, list):
        for one in images:
            if not isinstance(one, dict):
                continue
            url = one.get("default")
            if isinstance(url, str) and url.startswith("http"):
                return url
    return None


def features(card: dict[str, Any]) -> str:
    """The agent's bullet points as one string, for the prose filters."""

    bullets = card.get("features")
    if not isinstance(bullets, list):
        return ""
    return " \n".join(str(one) for one in bullets if isinstance(one, str))


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


def is_advert(card: Any) -> bool:
    """An in-market advert sitting in the results list rather than a listing.

    Two tells, both present: `"ad?": true`, and an integer `id` where every
    real listing carries a string one.
    """

    if not isinstance(card, dict):
        return True
    return bool(card.get("ad?")) or not isinstance(card.get("id"), str)


def as_listing(card: dict[str, Any], *, today: date | None = None) -> Listing | None:
    """One search card as a `Listing`, or nothing if it is unusable."""

    if not a_dwelling(card.get("humanised-property-type")):
        return None
    listing_id = card.get("id")
    price = monthly(card.get("price"))
    if not isinstance(listing_id, str) or not listing_id or price is None:
        return None

    address = SPACES.sub(" ", str(card.get("address") or "")).strip()
    postcode, district = where_in(address)
    prose = features(card)
    where = card.get("location")
    where = where if isinstance(where, dict) else {}
    reduced = bool(card.get("reduced?"))

    return Listing(
        source_key=SOURCE_KEY,
        external_id=listing_id,
        url=listing_url(listing_id),
        price_pcm=price,
        # A studio arrives with `bedrooms: null` rather than 0 — 11 of 11 in
        # the sample — and nought is this project's own word for one.
        bedrooms=_count(card.get("bedrooms"), default=0) or 0,
        bathrooms=_count(card.get("bathrooms")),
        property_type=kind_of(card.get("humanised-property-type")),
        # Furnishing is on the listing page only; `enrich` fills it, and
        # "unknown" until then lets the listing answer every furnishing filter
        # rather than the wrong one.
        furnished="unknown",
        # Nothing on a card states a pets policy. The page does, and a listing
        # that never mentions pets has not forbidden them, so it stays None.
        pets_allowed=None,
        bills_included=None,
        available_from=None,
        min_tenancy_months=None,
        deposit_pcm=None,
        postcode=postcode,
        postcode_district=district,
        lat=_coord(where.get("lat"), 90),
        lng=_coord(where.get("lon"), 180),
        title=address or None,
        description=None,
        # Agent listings only; OnTheMarket has no private-landlord channel.
        is_landlord_direct=False,
        photo_count=len(card["images"]) if isinstance(card.get("images"), list) else None,
        floor_area_sqft=None,
        raw={
            # The alert's "where" line reads `raw.address` rather than the
            # title; without this key the alert prints no address at all.
            "address": address,
            "property_type_said": str(card.get("humanised-property-type") or ""),
            "added_or_reduced": str(card.get("days-since-added-reduced") or ""),
            "reduced": reduced,
            "features": prose,
            "agent": str((card.get("agent") or {}).get("name") or ""),
        },
    )


@dataclass(frozen=True)
class Batch:
    """What one batch's whole window gave up, across every page of it."""

    caught: list[Catch] = field(default_factory=list)
    #: False when the window was not read through — a page shed as 404 and not
    #: recovered, a parameter the site did not honour, or the page cap. An
    #: incomplete read settles nothing, so the next run reads the same window.
    complete: bool = True
    pages: int = 0


@dataclass(frozen=True)
class Read:
    """What one batch page gave up."""

    caught: list[Catch] = field(default_factory=list)
    #: Cards that were not listings at all — the in-market advert slots.
    adverts: int = 0
    #: Cards a `Listing` could not be built from.
    invalid: int = 0


def catches_in(page: str, *, today: date | None = None) -> Read:
    """Every usable listing on one batch page."""

    cards = results_in(page).get("list")
    if not isinstance(cards, list):
        return Read()

    caught: list[Catch] = []
    adverts = 0
    invalid = 0
    for card in cards:
        if is_advert(card):
            adverts += 1
            continue
        try:
            listing = as_listing(card, today=today)
        except ValidationError:
            invalid += 1
            continue
        if listing is None:
            invalid += 1
            continue
        caught.append(
            Catch(
                listing=listing,
                image=picture(card),
                first_listed=first_listed(
                    card.get("days-since-added-reduced"),
                    reduced=bool(card.get("reduced?")),
                    today=today,
                ),
            )
        )
    return Read(caught=caught, adverts=adverts, invalid=invalid)


def letting_details(page: str) -> list[str]:
    """`property.lettingDetails.items` — the agent's own letting facts."""

    prop = _state(page).get("property")
    if not isinstance(prop, dict):
        return []
    items = (prop.get("lettingDetails") or {}).get("items")
    if not isinstance(items, list):
        return []
    return [str(one) for one in items if isinstance(one, str)]


def furnished_in(items: list[str]) -> Furnished:
    """One of the four words the filter offers.

    "Available as furnished or unfurnished" is deliberately `unknown`: it means
    the agent will do either, and `match._check_furnished` hands an unknown to
    everybody rather than to half. It is also the single most common item on
    these pages, so getting it wrong would mis-file most of the source.
    """

    for one in items:
        for word, pattern in FURNISHING:
            if pattern.search(one):
                return word
    return "unknown"


def available_in(items: list[str], *, today: date | None = None) -> date | None:
    """"Available now" or "Availability date: 29 Nov 2026"."""

    for one in items:
        if AVAILABLE_NOW.match(one.strip()):
            return today or today_in_london()
        found = AVAILABILITY.match(one.strip())
        if not found:
            continue
        try:
            return datetime.strptime(found.group(1).strip(), AVAILABLE_ON).date()
        except ValueError:
            return None
    return None


def months_in(items: list[str]) -> int | None:
    """The minimum term in months, preferring "Min. term" over the contract."""

    contract: int | None = None
    for one in items:
        found = MIN_TERM.match(one.strip())
        if found:
            return _count(found.group(1))
        offered = CONTRACT.match(one.strip())
        if offered and contract is None:
            contract = _count(offered.group(1))
    return contract


def deposit_in(items: list[str]) -> float | None:
    """"Deposit: £3000.00" as a number. The total, not a monthly share."""

    for one in items:
        found = DEPOSIT.match(one.strip())
        if not found:
            continue
        money = MONEY.search(found.group(1))
        if not money:
            return None
        try:
            return float(money.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def pets_in(page: str) -> bool | None:
    """True when the page carries the "Pet friendly" icon, else None.

    Never False: a listing that says nothing about pets has not forbidden
    them, and recording a refusal nobody made would hide flats from the people
    who need this filter most.
    """

    prop = _state(page).get("property")
    if not isinstance(prop, dict):
        return None
    icons = prop.get("iconLabels")
    if not isinstance(icons, list):
        return None
    for one in icons:
        if isinstance(one, dict) and str(one.get("title") or "").strip().lower() == PET_FRIENDLY:
            return True
    return None


def postcode_on(page: str, district: str | None = None) -> str | None:
    """The full postcode, from `headerData.dataLayer`.

    Stated on 14 of 14 pages read. The dataLayer is a JSON *string* inside the
    payload rather than an object, which is why it is parsed twice.

    When a district is given it has to agree, the same refusal `geo.nearest`
    and `rightmove.postcode_on` make: this page carries panels for similar
    properties, and a postcode belonging to one of those is not this flat's.
    """

    prop = _state(page).get("property")
    if not isinstance(prop, dict):
        return None
    raw = (prop.get("headerData") or {}).get("dataLayer")
    if not isinstance(raw, str):
        return None
    try:
        layer = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(layer, dict):
        return None
    stated = SPACES.sub(" ", str(layer.get("postcode") or "")).strip().upper()
    if not stated:
        return None
    full = FULL_POSTCODE.search(stated)
    if not full:
        return None
    if district and full.group(1) != district.strip().upper():
        return None
    return f"{full.group(1)} {full.group(2)}"


def area_on(page: str) -> int | None:
    """`minimumAreaSqFt`, already in square feet. Stated on about 7% of pages."""

    prop = _state(page).get("property")
    if not isinstance(prop, dict):
        return None
    stated = _count(prop.get("minimumAreaSqFt"))
    if stated:
        return stated
    # The prose form, "401 sq ft / 37 sq m", read for its unit rather than
    # assumed — see worker.units.
    return sqft_from(str(prop.get("minimumArea") or ""))


def exclusive_on(page: str) -> bool | None:
    """Whether the site says this listing is not on the other portals yet."""

    prop = _state(page).get("property")
    if not isinstance(prop, dict):
        return None
    said = prop.get("exclusive")
    return bool(said) if isinstance(said, bool) else None


def fill(listing: Listing, page: str, *, today: date | None = None) -> Listing:
    """The listing with whatever only its own page states.

    The card's values are never overwritten by a page that says nothing: every
    field below is only set when the page actually stated one.
    """

    items = letting_details(page)
    update: dict[str, Any] = {}

    furnished = furnished_in(items)
    if furnished != "unknown":
        update["furnished"] = furnished

    available = available_in(items, today=today)
    if available is not None:
        update["available_from"] = available

    months = months_in(items)
    if months is not None:
        update["min_tenancy_months"] = months

    deposit = deposit_in(items)
    if deposit is not None:
        update["deposit_pcm"] = deposit

    pets = pets_in(page)
    if pets is not None:
        update["pets_allowed"] = pets

    area = area_on(page)
    if area is not None:
        update["floor_area_sqft"] = area

    if listing.postcode is None:
        stated = postcode_on(page, listing.postcode_district)
        if stated is not None:
            update["postcode"] = stated
            update["postcode_source"] = "portal"

    exclusive = exclusive_on(page)
    if exclusive is not None or items:
        raw = dict(listing.raw)
        if exclusive is not None:
            raw["exclusive"] = exclusive
        if items:
            raw["letting_details"] = items
        update["raw"] = raw

    return listing.model_copy(update=update) if update else listing


@dataclass
class OnTheMarket:
    """The portal, as `worker.sources.sweep.collect` needs it.

    Not frozen: everything below the budgets is this run's working state, and
    `plan` resets it.

    A district reader that reads nine districts at a time. The engine still
    asks about one district per call — which is what keeps the watch, the
    watermark and the gap rule per district, where they belong — and the first
    call of each batch fetches the whole batch and answers the other eight from
    `_batched`. See `sweep.Portal.batch`.
    """

    key: str = SOURCE_KEY

    #: Day granularity, like Zoopla's `publishedOn`, and the site stands behind
    #: it. A reduced card states no date at all and is stored unannounced; see
    #: `first_listed`.
    dated: bool = True

    #: Districts per request. What `sweep.collect` reads to decide how many
    #: names to hand `plan`, and what `search_url` refuses to exceed.
    batch: int = BATCH

    #: Never a region reader. The whole-city read is the district reader with
    #: every district enabled, which is the same fourteen requests and needs no
    #: second job, no second timer and no pseudo-district.
    regions: tuple[str, ...] = ()

    max_pages: int = MAX_PAGES
    detail_budget: int = DETAIL_BUDGET
    resolve_budget: int = RESOLVE_BUDGET
    shed_budget: float = SHED_BUDGET
    shed_pause: float = SHED_PAUSE

    #: The batches this run will be asked about, in the order `collect` will
    #: ask. Empty until `plan` is called — a caller that never calls it gets
    #: one district per request, which is what the tests do.
    _batches: tuple[tuple[str, ...], ...] = field(default_factory=tuple, init=False)
    #: district -> what the batch read for it, for the eight calls per batch
    #: that cost nothing.
    _batched: dict[str, Harvest] = field(default_factory=dict, init=False)
    _details: int = field(default=-1, init=False)
    _resolves: int = field(default=-1, init=False)
    _shed_spent: float = field(default=0.0, init=False)
    #: What `enrich` read off each listing page, so `postcode_for` can have it
    #: without a second request. Absent rather than None when never read.
    _postcodes: dict[str, str | None] = field(default_factory=dict, init=False)

    def plan(self, districts: tuple[str, ...]) -> None:
        """The whole list this run will ask about, before the first call.

        Called by `sweep.collect` once, which is the only way this reader can
        know which districts may share a request: the engine computes them from
        the subscriber list and asks about them one at a time.

        Resets the run's state as well, so that a second sweep in one process
        — `python -m worker portals`, or a test — does not answer from the
        first one's pages.
        """

        names = tuple(dict.fromkeys(one.strip().upper() for one in districts if one.strip()))
        self._batches = tuple(
            names[start : start + self.batch] for start in range(0, len(names), self.batch)
        )
        self._batched = {}
        self._details = -1
        self._resolves = -1
        self._shed_spent = 0.0
        self._postcodes = {}

    def _batch_for(self, district: str) -> tuple[str, ...]:
        """Which districts share a request with this one."""

        for group in self._batches:
            if district in group:
                return group
        # Not planned — a direct caller, or a district that appeared after
        # `plan`. One district is always a valid batch.
        return (district,)

    def harvest(
        self,
        district: str,
        get: Fetcher,
        stage: Stage,
        since: datetime | None = None,
        memory: Memory | None = None,
    ) -> Harvest:
        """This district's last 24 hours.

        `since` is accepted and ignored. The window is fixed by the query, so
        there is no watermark to read back to and no ordering to follow — see
        the module note.
        """

        del since
        if district in self._batched:
            # Already read, as part of another district's request. Reported
            # with `pages=0` so the run's page count stays the number of pages
            # actually fetched.
            return self._batched.pop(district)

        group = self._batch_for(district)
        read = self._read_batch(group, get, stage)
        self._file(group, read, get, stage, memory)
        # `_file` wrote one entry per district in the group, including this one.
        return self._batched.pop(
            district, Harvest(caught=[], complete=read.complete, pages=read.pages)
        )

    def _read_batch(
        self, group: tuple[str, ...], get: Fetcher, stage: Stage
    ) -> Batch:
        """Every page of one batch's 24-hour window."""

        caught: list[Catch] = []
        pages = 0
        page_number = 1
        while page_number <= self.max_pages:
            body = self._page(group, page_number, get, stage)
            if body is None:
                # Shed and not recovered. The window for these districts was
                # not read through, so nothing settles and the next run — or
                # the next thirty-minute cache refresh — covers it again.
                return Batch(caught, complete=False, pages=pages)
            pages += 1
            wrong = honoured(body, group)
            if wrong:
                stage.degrade(
                    f"{'+'.join(group)}: {wrong} — refusing to read this page "
                    f"rather than report an empty district",
                )
                return Batch(caught, complete=False, pages=pages)
            read = catches_in(body)
            if read.adverts:
                stage.count("advert_slots", read.adverts)
            if read.invalid:
                stage.count("invalid", read.invalid)
            caught.extend(read.caught)
            if not next_page(body):
                return Batch(caught, complete=True, pages=pages)
            page_number += 1

        # The ceiling, which no measured batch has come near. Not complete:
        # there were more pages and we stopped asking.
        stage.count("page_cap")
        return Batch(caught, complete=False, pages=pages)

    def _page(
        self, group: tuple[str, ...], page_number: int, get: Fetcher, stage: Stage
    ) -> str | None:
        """One batch page, retried through a shed 404. None when it never came.

        `tolerate=(404,)` is the whole point: without it `Fetcher.get` raises
        `Refused`, the engine counts a refusal, and the district is dropped for
        the run on what is not a refusal at all. With it the status comes back
        as an ordinary reply and the decision is made here, where the 404's
        meaning is known.
        """

        url = search_url(group, page_number)
        while True:
            reply = get.get(url, tolerate=(404,))
            if reply.status != 404:
                return reply.body
            stage.count("shed")
            if self._shed_spent + self.shed_pause > self.shed_budget:
                stage.log(
                    "info",
                    f"{'+'.join(group)} page {page_number} was shed as 404 and this "
                    f"run has spent its waiting budget; the next run reads the same "
                    f"24 hours, so nothing is lost by stopping here",
                )
                return None
            self._shed_spent += self.shed_pause
            time.sleep(self.shed_pause)
            stage.count("shed_retried")

    def _file(
        self,
        group: tuple[str, ...],
        read: Batch,
        get: Fetcher,
        stage: Stage,
        memory: Memory | None,
    ) -> None:
        """Split one batch's listings across the districts that asked for them.

        A listing whose own district is not in the batch is dropped and
        counted: it is read in the batch that owns its district, or by nobody,
        and filing it here would judge it against another district's watch.
        """

        for district in group:
            self._batched[district] = Harvest(
                caught=[], complete=read.complete, pages=0
            )
        # The pages belong to whichever district triggered the request, so that
        # the run's page count is the pages fetched and not a multiple of it.
        self._batched[group[0]] = Harvest(
            caught=[], complete=read.complete, pages=read.pages
        )

        within = set(group)
        homeless = [one for one in read.caught if one.listing.postcode_district is None]
        resolved = self._resolve(homeless, get, stage, memory) if homeless else {}

        strays = 0
        for one in read.caught:
            where = one.listing.postcode_district
            if where is None:
                where = resolved.get(one.listing.external_id)
                if where is None:
                    strays += 1
                    continue
                one = replace(
                    one,
                    listing=one.listing.model_copy(
                        update={"postcode_district": where}
                    ),
                )
            if where not in within:
                strays += 1
                continue
            mine = self._batched[where]
            self._batched[where] = replace(mine, caught=[*mine.caught, one])
        if strays:
            # Not a fault. A batch of nine outcodes routinely returns a
            # neighbouring one — an SE16+SE8+E14 query returned an SE13 — and
            # the listing is read again in the batch that owns SE13 when SE13
            # is subscribed, or wanted by nobody when it is not.
            stage.count("outside_the_batch", strays)

    def _resolve(
        self,
        homeless: list[Catch],
        get: Fetcher,
        stage: Stage,
        memory: Memory | None,
    ) -> dict[str, str]:
        """external_id -> outward code, for cards whose address stated none.

        Each listing is asked about once ever, not once a run — the record is
        `memory.remember`, and 0053 is what happens without it: a third of a
        search's results re-resolved on every run, for ever.

        The postcode itself is deliberately NOT returned, only the district.
        Leaving `postcode` None keeps the engine's own order intact: the
        listing's page is read for the real postcode when somebody subscribes
        to the district, and `_derive_postcodes` covers the rest. Filling it in
        here would pre-empt both, and with a value `store.mark_duplicate` must
        not merge on.
        """

        ids = [one.listing.external_id for one in homeless]
        known = memory.resolved(ids) if memory else {}
        found: dict[str, str] = {}
        learned: dict[str, str | None] = {}

        for one in homeless:
            external_id = one.listing.external_id
            if external_id in known:
                # Including a remembered None: looked once, the page said
                # nothing, and asking again would buy the same silence.
                district = known[external_id]
            else:
                if self._resolves < 0:
                    self._resolves = self.resolve_budget
                if self._resolves <= 0:
                    stage.count("district_over_budget")
                    continue
                self._resolves -= 1
                district = self._district_of(one, get, stage)
                learned[external_id] = district
            if district is None:
                continue
            found[external_id] = district
            stage.count("district_resolved")

        if learned and memory:
            memory.remember(learned)
        return found

    def _district_of(self, catch: Catch, get: Fetcher, stage: Stage) -> str | None:
        """The outward code, from the listing's own page."""

        try:
            reply = get.get(catch.listing.url)
        except (Refused, OSError):
            stage.count("district_unreachable")
            return None
        stated = postcode_on(reply.body)
        # Kept whatever happens next: `enrich` and `postcode_for` both want
        # this page and neither should fetch it twice.
        self._postcodes[catch.listing.external_id] = stated
        if stated is None:
            return None
        outward = stated.split(" ", 1)[0]
        return outward if OUTCODE.fullmatch(outward) else None

    def enrich(self, catch: Catch, get: Fetcher, stage: Stage) -> Catch:
        """The four filters a card cannot answer, from the listing's page.

        Furnishing, availability, minimum term and floor area are on this page
        and nowhere else, and so is the full postcode `store.mark_duplicate`
        needs — which on an agents-only portal syndicated to Rightmove and
        Zoopla is most of why the page is read at all.

        Often free: `_district_of` may already have read this page for a card
        whose address stated no outward code, and its answer is reused.
        """

        if self._details < 0:
            self._details = self.detail_budget
        if self._details <= 0:
            stage.count("detail_over_budget")
            return catch
        self._details -= 1

        reply = get.get(catch.listing.url)
        self._postcodes[catch.listing.external_id] = postcode_on(
            reply.body, catch.listing.postcode_district
        )
        try:
            filled = fill(catch.listing, reply.body)
        except ValidationError:
            # The card already validated, so this is the page contradicting it.
            # Keeping the card's version is the conservative answer.
            stage.count("detail_invalid")
            return catch
        stage.count("detail_pages")
        return catch if filled is catch.listing else replace(catch, listing=filled)

    def postcode_for(self, catch: Catch, get: Fetcher, stage: Stage) -> str | None:
        """The full postcode, from the listing's own page.

        Normally free: `enrich` has already read that page this run and left
        the answer behind. The fetch here is for the listings it did not reach
        — one whose page would not load, and one past `detail_budget` — so that
        the postcode keeps its own budget and does not inherit enrich's.
        """

        del stage
        # Membership, not the value: "looked and the page did not say" and
        # "never looked" are both None and only the second is worth a request.
        if catch.listing.external_id in self._postcodes:
            return self._postcodes[catch.listing.external_id]
        reply = get.get(catch.listing.url)
        return postcode_on(reply.body, catch.listing.postcode_district)


__all__ = [
    "BASE",
    "BATCH",
    "DETAIL_BUDGET",
    "MAX_PAGES",
    "PAGE_SIZE",
    "RECENT",
    "RESOLVE_BUDGET",
    "SHED_BUDGET",
    "SHED_PAUSE",
    "SOURCE_KEY",
    "Batch",
    "Catch",
    "OnTheMarket",
    "Read",
    "a_dwelling",
    "area_on",
    "as_listing",
    "available_in",
    "catches_in",
    "deposit_in",
    "exclusive_on",
    "features",
    "fill",
    "first_listed",
    "furnished_in",
    "honoured",
    "is_advert",
    "kind_of",
    "letting_details",
    "listing_url",
    "monthly",
    "months_in",
    "next_data",
    "next_page",
    "pets_in",
    "picture",
    "postcode_on",
    "query_in",
    "results_in",
    "search_url",
    "today_in_london",
    "where_in",
]
