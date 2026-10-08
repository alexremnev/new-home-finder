"""OpenRent, read from its district search pages.

The Telegram feed does not publish OpenRent, so it is the one portal that has
to be fetched directly. Everything after this module is unchanged: a listing
goes into `listings` like any other, and match, queue and drain never learn
where it came from.

── how listings are discovered ───────────────────────────────────────────────

`robots.txt`, re-read on 26 September 2026, does not disallow
`/properties-to-rent/`, and one district page carries:

  * `var PROPERTYIDS = [ 2937375, 3030969, … ]` — **every** listing id the
    search matches, not only the twenty rendered. E14 returned 328 ids in one
    92KB page, with `NUMBEROFPROPERTIES = 328` beside it to check against.
  * the first twenty as full cards: the slug, the monthly rent, and a
    CDN-resized photograph.

So discovery costs one request per district, and it scales with the districts
somebody actually subscribes to rather than with the size of the country.

── why not the sitemap ──────────────────────────────────────────────────────

There was a second reader here until October 2026 which discovered from the
nationwide sitemap, because that is the only thing `robots.txt` pointed at. It
is gone, and the measurements that retired it are worth keeping: that sitemap
carries no `lastmod`, no `ETag` and no `Last-Modified`, ignores `Range`, and is
served uncompressed whatever `Accept-Encoding` asks for. So every run
downloaded the whole of the United Kingdom to find out what changed in E14 —
about 950MB a day to discover two or three listings, against roughly a
fiftieth of that here.

── the district is not the search ───────────────────────────────────────────

`var SEARCHRADIUS = 2` — the district page is a two-kilometre radius, not the
outcode. About a third of what it returns is in a neighbouring district, so
unlike Rightmove the searched district cannot be trusted as the listing's own.

The id alone does not say either. What does is the slug, and a bare id redirects
to it:

    GET /2937375  ->  301  Location: /property-to-rent/london/
                           room-in-a-shared-flat-willis-house-e14/2937375

Those headers are about 3KB where the listing page is 300KB, so one HEAD per
unknown id answers "is this even in a district we care about" for a hundredth
of the cost of finding out by reading the page. The slug also gives the bedroom
count and the property type — see `read_slug`.

── what still needs the listing page ───────────────────────────────────────

The rent. The cards carry it for the twenty that are rendered, but a district
has hundreds, so the page is still fetched for each genuinely new listing in a
subscribed district, and only for those. The deposit, the minimum tenancy, the
full postcode and the availability date come with it.

── why the page is read as text ──────────────────────────────────────────────

The details sit in label-and-value blocks whose markup is not stable, so the
page is stripped to text and the values are found by their labels: rows get
reordered, labels do not. The one exception is the postcode, which is only
available inside a link, so it is taken from the HTML before stripping.

── why this source is undated ──────────────────────────────────────────────

OpenRent publishes no listing date anywhere this reader can see it. The search
has a "New" sort in its dropdown, but the value does not take as a query
parameter — tried 0 through 6, and the order never changed — and the cards say
only "Last updated around 2 weeks ago". So there is no watermark to compare
against, and this source uses the engine's read-through rule: a district is
read silently until a run finds nothing new left in it, and only then does it
start announcing. See worker.sources.sweep.
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError

from worker.contracts.listing import Listing
from worker.ingest.photo import image_in
from worker.obs.log import Stage
from worker.sources.fetch import BLOCKED as REFUSALS
from worker.sources.fetch import Fetcher, Refused
from worker.sources.sweep import Catch, Harvest, Memory
from worker.units import sqft_from

SOURCE_KEY = "openrent"
BASE = "https://www.openrent.co.uk"

# Listing pages per run, shared across every district. A first look at a busy
# district finds hundreds of unknown ids; fetching them all at once would be
# both rude and slow, so a run takes a slice and the district converges over
# several. It cannot settle early while that is happening: a truncated run
# reports `complete=False`, and the engine only settles on a complete one.
DETAIL_BUDGET = 40

# Redirect lookups per run. Cheap — about 3KB each — but not free, and the
# same reasoning applies.
SLUG_BUDGET = 400

# Between listing pages. No crawl delay is published, so this is manners.
PAUSE_SECONDS = 1.0

# ── what a search page gives up ─────────────────────────────────────────────

# Every id the search matched, which is more than the page renders.
PROPERTY_IDS = re.compile(r"var\s+PROPERTYIDS\s*=\s*\[([^\]]*)\]", re.IGNORECASE)
HOW_MANY = re.compile(r"var\s+NUMBEROFPROPERTIES\s*=\s*(\d+)", re.IGNORECASE)
# The twenty rendered cards: the slug and the id, straight from the href.
CARD = re.compile(
    r'href="(?P<path>/property-to-rent/[^/"]+/(?P<slug>[^/"]+)/(?P<id>\d+))"',
    re.IGNORECASE,
)
# `//imagescdn.openrent.co.uk/listings/2937375/o_….JPG_homepage.JPG` — already
# resized by them, and on a CDN that serves anybody.
#
# Keyed on the id *inside* the url, not on the `data-listing-id` of whatever
# card the url happens to sit near. The first version of this matched forwards
# from `data-listing-id` to the next `src`, which quietly crossed card
# boundaries: listing 3030969 came back holding listing 284912's photograph,
# so an alert would have shown the wrong flat. The url identifies itself, so
# there is nothing to correlate and nothing to get wrong.
CARD_IMAGE = re.compile(
    r'//imagescdn\.openrent\.co\.uk/listings/(?P<id>\d+)/(?P<rest>[^"\s]+)',
    re.IGNORECASE,
)

# ── what a slug and a listing page give up ──────────────────────────────────

# e14, se16, wc2n, cv10, al1 — the outward half of a UK postcode.
OUTWARD = re.compile(r"^[a-z]{1,2}\d{1,2}[a-z]?$", re.IGNORECASE)

TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
SPACES = re.compile(r"\s+")

# Confirmed against a live page: "Rent £1,000.00 per month", "£1,000.00 p/m",
# "Deposit / Bond is £1,000.00", "1 bathrooms", "Minimum Tenancy 6 Months",
# and the postcode inside a comparebroadband link.
# Tried in order, so the most explicit statement of the rent wins. The bare
# "per month" form is third because it also catches the monthly figure a
# weekly-priced listing gives in brackets — "£2,950pw (£12,783 per month)" —
# which is the number we want and the only one stated per month.
RENT = (
    re.compile(r"Rent\s*£\s*([\d,]+(?:\.\d{2})?)\s*per\s*month", re.IGNORECASE),
    re.compile(r"To\s*Rent\s*Now\s*for\s*£\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE),
    re.compile(r"£\s*([\d,]+(?:\.\d{2})?)\s*per\s*month", re.IGNORECASE),
    re.compile(r"£\s*([\d,]+(?:\.\d{2})?)\s*p\s*/\s*m", re.IGNORECASE),
    re.compile(r"£\s*([\d,]+(?:\.\d{2})?)\s*pcm", re.IGNORECASE),
)
DEPOSIT = re.compile(
    r"Deposit\s*/?\s*Bond\s*(?:is)?\s*£\s*([\d,]+(?:\.\d{2})?)", re.IGNORECASE
)
BATHROOMS = re.compile(r"(\d+)\s*bathrooms?\b", re.IGNORECASE)
TENANCY = re.compile(r"Minimum\s*Tenancy[^\d]{0,40}(\d+)\s*Month", re.IGNORECASE)
AVAILABLE = re.compile(
    r"Available\s*(?:From)?[^A-Za-z0-9]{0,20}(Today|Now|\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)
POSTCODE_IN_LINK = re.compile(r"postCode=([A-Za-z0-9%+\s]+)", re.IGNORECASE)

FURNISHING = (
    ("part", re.compile(r"\bpart[\s-]*furnished\b", re.IGNORECASE)),
    ("unfurnished", re.compile(r"\bunfurnished\b", re.IGNORECASE)),
    ("furnished", re.compile(r"\bfurnished\b", re.IGNORECASE)),
)

# What OpenRent puts between the bedroom count and the street, longest first
# so that `terraced-house` is matched before `terraced` and `semi-detached`
# before `detached`. Collected from a hundred live slugs across five districts:
# flat, terraced, maisonette, end-of-terrace, semi-detached.
SLUG_KINDS: tuple[tuple[str, str], ...] = (
    ("end-of-terrace-house", "house"),
    ("semi-detached-house", "house"),
    ("end-of-terrace", "house"),
    ("semi-detached", "house"),
    ("terraced-house", "house"),
    ("detached-house", "house"),
    ("town-house", "house"),
    ("townhouse", "house"),
    ("terraced", "house"),
    ("detached", "house"),
    ("bungalow", "house"),
    ("cottage", "house"),
    ("house", "house"),
    ("maisonette", "flat"),
    ("apartment", "flat"),
    ("penthouse", "flat"),
    ("duplex", "flat"),
    ("studio", "flat"),
    ("flat", "flat"),
)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# The address, which on this site exists only in the page's <h1>.
#
# Measured against four live pages on 2 October 2026, one of each shape the
# slug can take, and the form held for all four:
#
#   2 Bed Flat, Discovery Dock, E14
#   4 Bed Maisonette, Smythe St, E14
#   Room in a Shared Flat, Willis House, E14
#   Studio Flat, London, E14
#
# So: `<what it is>, <where it is>, <outcode>`. The first part is the bedroom
# count and the type, which the alert prints on its own line, and the last is
# the outcode, which it prints on the line above — which leaves the middle,
# and the middle alone, as the thing that belongs on the "where" line.
#
# Not taken from the slug, although the same words are in it. `smythe-st`
# title-cased is "Smythe St" by luck rather than by rule, and the heading is
# what the landlord actually typed.
HEADING = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)

# What OpenRent puts where a building name would go when the landlord gave
# none — see the studio above. "London" under a line already reading "E14" is
# a line that says nothing, so there is no line.
NO_PLACE = frozenset({"london", "greater london"})


@dataclass(frozen=True)
class Found:
    """What the id and its slug reveal, before the listing page is fetched."""

    external_id: str
    url: str
    district: str
    bedrooms: int
    property_type: str | None


def as_text(markup: str) -> str:

    # Entities are decoded, and after the tags rather than before: the pound
    # sign arrives as "&#xA3;" on most of this site, so matching a literal "£"
    # found nothing on pages that plainly showed a price. Decoding first would
    # let an escaped "&lt;script&gt;" become a tag after the stripping that was
    # supposed to remove it.
    return SPACES.sub(" ", html.unescape(TAGS.sub(" ", markup))).strip()


def money(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def address_in(markup: str) -> str | None:
    """Where the listing is, out of the page's heading. See `HEADING`."""

    found = HEADING.search(markup)
    if not found:
        return None

    parts = [one.strip() for one in as_text(found.group(1)).split(",")]
    # Three at a minimum. Anything shorter is not the form measured above, and
    # deciding which half of two parts is the address would be a guess — so
    # the honest answer is the one this had before: no address line.
    if len(parts) < 3 or not OUTWARD.match(parts[-1]):
        return None

    middle = ", ".join(one for one in parts[1:-1] if one)
    return None if not middle or middle.lower() in NO_PLACE else middle


def place_in_slug(slug: str) -> str | None:
    """The street or building out of a listing slug, title-cased.

    The same `<n>-bed-<type>-<street or building>-<outcode>` shape `read_slug`
    takes the type out of, read for the part in the middle.

    Weaker than `address_in` and only for where there is no page to read it
    from: a listing that reached us through the Telegram feed has its url and
    whatever the message stated, and a message that stated no address left the
    alert with no "where" line at all. The words here are the ones the landlord
    typed; their punctuation and capitals are not, so "Smythe St" comes back
    right and a flat number loses its comma.
    """

    head, _, tail = slug.rpartition("-")
    if not OUTWARD.match(tail) or not head:
        return None

    words = head.lower()
    if "room-in-a-shared" in words:
        _, _, rest = words.partition("room-in-a-shared-")
        # "flat" or "house" and then the place.
        rest = rest.partition("-")[2]
    elif words.startswith("studio"):
        rest = words[len("studio"):].lstrip("-")
        rest = rest[len("flat"):].lstrip("-") if rest.startswith("flat") else rest
    else:
        beds = re.match(r"\d+-bed", words)
        if not beds:
            return None
        rest = words[beds.end():].lstrip("-")
        for name, _kind in SLUG_KINDS:
            if rest == name or rest.startswith(name + "-"):
                rest = rest[len(name):].lstrip("-")
                break

    place = " ".join(one.capitalize() for one in rest.split("-") if one)
    return None if not place or place.lower() in NO_PLACE else place


def read_slug(slug: str) -> tuple[str, int, str | None] | None:
    """The district, the bedroom count and the type, from the URL alone."""

    head, _, tail = slug.rpartition("-")
    if not OUTWARD.match(tail):
        return None
    district = tail.upper()
    words = head.lower()

    if "room-in-a-shared" in words:
        # One room in somebody else's flat. The count is not a bedroom count and
        # is never shown for this type.
        return district, 1, "room"
    if words.startswith("studio"):
        # A flat with no separate bedroom — see tg_feed.bedrooms_of. The zero is
        # what says "studio", and the bedroom slider reads it back as the word.
        return district, 0, "flat"

    beds = re.match(r"(\d+)-bed", words)
    if not beds:
        return None

    # The type is whatever follows the bedroom count, and only that.
    #
    # It used to be any of these words found anywhere in the slug, which is
    # wrong in a way that was quietly costing real listings: a flat in a
    # building called "St Cuthbert House" has the slug
    # `2-bed-flat-st-cuthbert-house-e14`, "house" matched before "flat" was
    # tried, and the flat was stored as a house. Of a hundred slugs sampled
    # across five districts, `1-bed-flat-claremont-house-se16`,
    # `2-bed-flat-vancouver-house-se16`, `1-bed-flat-durell-house-se16` and
    # `2-bed-flat-bluebell-house-se16` were all flats recorded as houses —
    # invisible to anyone filtering for a flat and wrongly shown to anyone
    # filtering for a house. London is full of buildings called "… House".
    #
    # OpenRent's slug is `<n>-bed-<type>-<street or building>-<outcode>`, so
    # the type has a position. Matched longest first, because `terraced-house`
    # and `terraced` both appear and the longer one must win.
    #
    # Still narrowed to four words on purpose: the matcher compares the stored
    # type against the filter as an exact string, so "terraced house" would not
    # answer a filter for "house". The portal's own phrase stays in `raw`.
    after = words[beds.end() - beds.start():].lstrip("-")
    for name, kind in SLUG_KINDS:
        if after == name or after.startswith(name + "-"):
            return district, int(beds.group(1)), kind
    return district, int(beds.group(1)), None


def when(raw: str) -> date | None:
    lowered = raw.strip().lower()
    if lowered in ("today", "now"):
        return date.today()
    parts = lowered.split()
    if len(parts) == 3 and parts[1] in MONTHS:
        try:
            return date(int(parts[2]), MONTHS[parts[1]], int(parts[0]))
        except ValueError:
            return None
    return None


def as_listing(
    found: Found, html: str, *, source_key: str = SOURCE_KEY
) -> Listing | None:
    """A listing, or nothing if the page did not give a price.

    `source_key` is a parameter rather than this module's constant because the
    reader carries its own key — see `OpenRent.key`. It was hard-coded once,
    and a reader whose key differed from it stored everything under the wrong
    source while asking whether it had seen an id under the right one, so the
    answer was always no and the same hundreds of pages were re-fetched every
    twenty minutes. See 0053.
    """

    # Before stripping: the postcode exists only inside a link.
    postcode = None
    in_link = POSTCODE_IN_LINK.search(html)
    if in_link:
        cleaned = in_link.group(1).replace("%20", " ").replace("+", " ").strip()
        postcode = SPACES.sub(" ", cleaned).upper() or None

    text = as_text(html)

    price = None
    for pattern in RENT:
        hit = pattern.search(text)
        if hit:
            price = money(hit.group(1))
            break
    if price is None or not (100 <= price <= 100_000):
        # Without a price there is nothing to match on, so the listing is
        # skipped rather than stored half-formed. The caller counts these.
        return None

    deposit = DEPOSIT.search(text)
    baths = BATHROOMS.search(text)
    tenancy = TENANCY.search(text)
    available = AVAILABLE.search(text)

    furnished = "unknown"
    for name, pattern in FURNISHING:
        if pattern.search(text):
            furnished = name
            break

    # Read before `as_text` would be reused on the whole page: the heading is
    # markup, and the one thing on this page that names the address.
    address = address_in(html)

    return Listing(
        source_key=source_key,
        external_id=found.external_id,
        url=found.url,
        price_pcm=int(round(price)),
        bedrooms=found.bedrooms,
        bathrooms=int(baths.group(1)) if baths else None,
        property_type=found.property_type,
        furnished=furnished,  # type: ignore[arg-type]
        available_from=when(available.group(1)) if available else None,
        min_tenancy_months=int(tenancy.group(1)) if tenancy else None,
        deposit_pcm=money(deposit.group(1)) if deposit else None,
        # These pages state it in metres — "105 sq m" — and the unit is read
        # from the text rather than assumed. See worker.units.
        floor_area_sqft=sqft_from(text),
        postcode=postcode,
        postcode_district=found.district,
        # Every OpenRent listing is let by the landlord; that is the site.
        is_landlord_direct=True,
        title=address,
        raw={
            # The exact phrase from the url, kept because `property_type` is
            # deliberately narrowed to four words for filtering.
            "slug": found.url.rsplit("/", 2)[-2] if "/" in found.url else "",
            # The alert's "where" line reads `raw.address` and not the listing
            # title — the same key Rightmove and Zoopla write under. Without
            # it an OpenRent alert printed no address at all, which is how this
            # was noticed: a postcode, a price and no idea where the flat is.
            "address": address or "",
        },
    )


def search_url(district: str) -> str:
    """The search page for one district."""

    return f"{BASE}/properties-to-rent/{district.lower()}"


def short_url(external_id: str) -> str:
    """The bare-id url that redirects to the slug. See the module note."""

    return f"{BASE}/{external_id}"


def ids_in(page: str) -> list[str]:
    """Every listing id the search matched, in the order given."""

    found = PROPERTY_IDS.search(page)
    if not found:
        return []
    out: list[str] = []
    for piece in found.group(1).split(","):
        digits = piece.strip()
        if digits.isdigit():
            out.append(digits)
    return out


def how_many(page: str) -> int | None:
    """The count the page states, to check the id list against."""

    found = HOW_MANY.search(page)
    return int(found.group(1)) if found else None


def slugs_in(page: str) -> dict[str, str]:
    """id -> slug, for the cards the page rendered."""

    return {
        found.group("id"): found.group("slug") for found in CARD.finditer(page)
    }


def paths_in(page: str) -> dict[str, str]:
    """id -> listing path, for the cards the page rendered."""

    return {
        found.group("id"): found.group("path") for found in CARD.finditer(page)
    }


def images_in(page: str) -> dict[str, str]:
    """id -> preview picture url, for the cards the page rendered.

    Already the size OpenRent shows in its own results, on a CDN that serves
    any client. Nothing is downloaded here: the url is stored and Telegram and
    WhatsApp fetch it themselves. See worker.sources.sweep.Catch.
    """

    out: dict[str, str] = {}
    for found in CARD_IMAGE.finditer(page):
        listing_id = found.group("id")
        if listing_id not in out:
            # Protocol-relative in the markup, which no messenger will follow.
            out[listing_id] = (
                f"https://imagescdn.openrent.co.uk/listings/{listing_id}/"
                f"{found.group('rest')}"
            )[:1000]
    return out


def slug_from_path(path: str) -> str | None:
    """The slug out of a listing path, whether relative or absolute."""

    found = CARD.search(f'href="{path}"')
    return found.group("slug") if found else None


@dataclass
class OpenRent:
    """The portal, as `worker.sources.sweep.collect` needs it.

    Not frozen, and not by accident: `_details` and `_slugs` are this run's
    remaining budgets and they are spent across districts, so that one busy
    district cannot consume the whole run.
    """

    key: str = SOURCE_KEY
    #: No listing date is published anywhere this reader can see. See the
    #: module note, and the read-through rule in worker.sources.sweep.
    dated: bool = False
    detail_budget: int = DETAIL_BUDGET
    slug_budget: int = SLUG_BUDGET
    pause: float = PAUSE_SECONDS

    _details: int = field(default=-1, init=False)
    _slugs: int = field(default=-1, init=False)

    def _start(self) -> None:
        if self._details < 0:
            self._details = self.detail_budget
        if self._slugs < 0:
            self._slugs = self.slug_budget

    def harvest(
        self,
        district: str,
        get: Fetcher,
        stage: Stage,
        since: Any = None,
        memory: Memory | None = None,
    ) -> Harvest:
        self._start()
        # For an undated portal the engine passes `settled_at`, so None here
        # means this district has never been read through. That distinction is
        # what makes the first pass cheap — see `backfill` below.
        settled = since is not None

        reply = get.get(search_url(district))
        ids = ids_in(reply.body)
        stated = how_many(reply.body)
        if stated is not None and ids and abs(stated - len(ids)) > 1:
            # The two disagree, which means the page shape has moved. Said out
            # loud rather than trusted, because the id list is the whole basis
            # of this reader.
            stage.log(
                "warn",
                f"{district}: the page says {stated} properties but lists "
                f"{len(ids)} ids; reading what is listed",
            )
        if not ids:
            # No ids at all is either an empty district or a changed page. It
            # is reported as complete so an empty district can settle, which
            # is what lets it announce its first real listing.
            stage.count("no_ids")
            return Harvest(caught=[], complete=True, pages=1)

        # Asked once for the whole list, not once per id: this is a database
        # round trip, and a district is hundreds of ids.
        stored = memory.stored(ids) if memory else set()
        # And separately: ids we have already resolved but did NOT store,
        # because their slug put them in a neighbouring district. The search is
        # a two-kilometre radius, so that is about a third of what comes back.
        # Without this they were re-resolved on every run for ever. See 0053.
        resolved = memory.resolved(ids) if memory else {}
        unknown = [
            one for one in ids if one not in stored and one not in resolved
        ]
        stage.count("in_radius", len(ids))
        stage.count("already_known", len(ids) - len(unknown))
        stage.count("already_resolved", sum(1 for one in ids if one in resolved))

        # Everything learned this pass, written once at the end.
        learned: dict[str, str | None] = {}

        # ── the first pass, which is where this reader was getting stuck ────
        #
        # An undated portal settles a district by reading it through — see the
        # rule in worker.sources.sweep — and until it settles, `_announceable`
        # refuses everything, so nothing in it is ever sent. This reader never
        # got there: a district is about 330 ids, the slug budget is 400 for
        # the whole run across ten districts, so the budget ran out, the read
        # was reported incomplete, the district never settled, and the next run
        # started again. Measured over a fortnight: 2,413 part-reads, 721 runs,
        # and not one listing announced.
        #
        # The resolving was never the point of a first pass. Nothing found in
        # an unsettled district can be announced whatever we learn about it, so
        # the only thing the first pass owes the next one is "these ids are not
        # new". Writing them down is one database round trip; asking OpenRent
        # about each of them is 330 requests that buy nothing.
        #
        # So: record the lot, settle the district, and start announcing
        # tomorrow. The cost is that existing stock in a brand new district is
        # never stored — which is exactly what the read-through rule means by
        # settling, and what it was already doing on purpose for every listing
        # the budget did reach.
        if not settled:
            stage.count("backfilled", len(unknown))
            if memory is not None and unknown:
                memory.remember(dict.fromkeys(unknown, None))
            return Harvest(caught=[], complete=True, pages=1)

        slugs = slugs_in(reply.body)
        pictures = images_in(reply.body)
        paths = paths_in(reply.body)

        caught: list[Catch] = []
        complete = True
        # One search page, which is what this reader reads. The redirect
        # lookups and the listing pages are requests, not pages, and they get
        # their own counters below — `pages` was counting all three, so a run
        # that spent its slug budget reported 439 "pages" and the admin's
        # chart of pages per run meant nothing.
        lookups = 0
        details = 0

        for listing_id in unknown:
            slug = slugs.get(listing_id)
            path = paths.get(listing_id)

            if slug is None:
                # Not one of the rendered cards, so ask the redirect. This is
                # the cheap question that keeps us from reading a 300KB page
                # for a flat in the next district along.
                if self._slugs <= 0:
                    complete = False
                    break
                self._slugs -= 1
                lookups += 1
                try:
                    status, where = get.head(short_url(listing_id))
                except (Refused, OSError) as error:
                    # Could not ask, so nothing is learned and the district is
                    # not settled on the strength of it.
                    stage.count("slug_unreachable")
                    stage.log("warn", f"{listing_id}: {type(error).__name__}: {error}")
                    complete = False
                    continue
                if not where or status not in (301, 302, 307, 308):
                    # Two different things, and conflating them is what made
                    # this reader ask the same questions for ever.
                    #
                    # A refusal — 401, 403, 405, 429 — means we could not ask.
                    # Nothing is learned, the district stays incomplete, and the
                    # id comes round again next run. `Fetcher.head` has already
                    # tried the proxy by this point.
                    #
                    # Any other answer is an answer: OpenRent replied and there
                    # is no slug behind that id. Written down, so it is asked
                    # about once rather than on every run — which is what 0053
                    # exists for — and the read is NOT called incomplete,
                    # because there is nothing left to come back for.
                    if status in REFUSALS:
                        stage.count("slug_refused")
                        complete = False
                    else:
                        stage.count("no_slug")
                        learned[listing_id] = None
                    continue
                path = where
                slug = slug_from_path(where)
                if slug is None:
                    stage.count("no_slug")
                    continue

            read = read_slug(slug)
            if read is None:
                # A slug shape `read_slug` does not recognise. Counted rather
                # than guessed at, and remembered so we do not ask about this
                # id again.
                stage.count("unreadable_slug")
                learned[listing_id] = None
                continue
            where_it_is, _beds, _kind = read
            learned[listing_id] = where_it_is
            if where_it_is != district.upper():
                # The two-kilometre radius, doing what it does. Not an error,
                # and not ours to store under this district.
                stage.count("outside_district")
                continue

            if self._details <= 0:
                # Out of budget for this run. The district is deliberately
                # left incomplete so that the engine does not settle it: there
                # is still unread stock in it, and settling now would announce
                # that stock as new on the next run.
                complete = False
                break
            self._details -= 1
            if self._details < self.detail_budget - 1:
                time.sleep(self.pause)

            url = f"{BASE}{path}" if path and path.startswith("/") else None
            if url is None:
                stage.count("no_path")
                continue
            try:
                detail = get.get(url)
            except (Refused, OSError) as error:
                stage.count("unreachable")
                stage.log("warn", f"{listing_id}: {type(error).__name__}: {error}")
                complete = False
                continue
            details += 1

            found = _found(listing_id, url, where_it_is, read)
            try:
                # This reader's own key, not the parser's default. See 0053.
                listing = as_listing(found, detail.body, source_key=self.key)
            except ValidationError as error:
                # Never fatal. One listing the contract refuses cost a whole
                # run once already: a scrape died on a url that said `21-bed`,
                # and forty districts went unread because of one house. The
                # ceiling has since been raised, but the lesson is the crash.
                #
                # Only validation, deliberately. That is a judgement about
                # their data and skipping it is right; a database error is a
                # problem with ours and should still stop the run.
                stage.count("invalid")
                stage.log(
                    "warn",
                    f"{listing_id}: refused by the listing contract — "
                    f"{str(error).splitlines()[0]}",
                )
                continue
            if listing is None:
                stage.count("no_price")
                continue

            caught.append(
                Catch(listing=listing, image=pictures.get(listing_id) or _og(detail.body))
            )

        if memory is not None and learned:
            memory.remember(learned)
        stage.count("resolved", len(caught))
        stage.count("slug_lookups", lookups)
        stage.count("detail_pages", details)
        return Harvest(caught=caught, complete=complete, pages=1)


def _found(listing_id: str, url: str, district: str, read: tuple[str, int, str | None]) -> Found:
    """A `Found`, built from what the slug gave us."""

    _where, beds, kind = read
    return Found(
        external_id=listing_id,
        url=url,
        district=district,
        bedrooms=beds,
        property_type=kind,
    )


def _og(page: str) -> str | None:
    """The picture from the listing page, when the card did not carry one."""

    return image_in(page)


__all__ = [
    "BASE", "DETAIL_BUDGET", "PAUSE_SECONDS", "SLUG_BUDGET", "SLUG_KINDS",
    "SOURCE_KEY", "Found", "OpenRent", "address_in", "as_listing", "as_text",
    "how_many", "ids_in", "images_in", "paths_in", "place_in_slug",
    "read_slug", "search_url", "short_url", "slug_from_path", "slugs_in",
    "when",
]
