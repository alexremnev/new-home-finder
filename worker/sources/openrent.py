"""OpenRent, read from the site itself.

The Telegram feed does not publish OpenRent, so it is the one portal that has to
be fetched directly. Everything after this module is unchanged: a listing goes
into `listings` like any other, and match, queue and drain never learn where it
came from.

── how listings are discovered ───────────────────────────────────────────────

From the site's own sitemap, which `robots.txt` declares and permits. Checked on
21 September 2026: `robots.txt` allows `/property-to-rent/`, names
`sitemap.xml`, and sets no crawl delay.

The sitemap carries no `lastmod`, so "new" cannot be read from it — it is
established by comparing the ids it lists against the ids already stored.

── why the district filter comes first ───────────────────────────────────────

The districts come from live subscriptions — what somebody is waiting to hear
about — not from a list of what this source is said to cover. Those two
disagreed in both directions: fetching districts nobody had chosen, and never
fetching one that somebody had.

The sitemap is nationwide: Doncaster and Reading sit beside London. But a
listing URL ends with its own outward code —
`/property-to-rent/nuneaton/3-bed-terraced-house-mallard-avenue-cv10/55214` —
so the district filter is applied to the URL, before a single listing page is
requested. That is the difference between tens of requests per run and one per
new listing in the country.

The slug also gives the bedroom count and the property type, which is why a page
is only fetched for the one thing it alone holds: the price.

── why the page is read as text ──────────────────────────────────────────────

The details sit in label-and-value blocks whose markup is not stable, so the
page is stripped to text and the values are found by their labels: rows get
reordered, labels do not. The one exception is the postcode, which is only
available inside a link, so it is taken from the HTML before stripping.
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

from worker import store
from worker.contracts.listing import Listing
from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

SOURCE_KEY = "openrent"
BASE = "https://www.openrent.co.uk"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"

# Identifies us and says where to complain, which is the least a scraper owes a
# site that allowed it.
AGENT = "LondonHomeFinderBot/1.0 (+https://londonhomefinder.co.uk)"

# Pages per run. The sitemap is fetched whole every time, but listing pages are
# only fetched for ids we have never seen, and a first run must not try to read
# the whole of London in one go.
PAGE_BUDGET = 40

# Between listing pages. No crawl delay is published, so this is manners rather
# than obedience.
PAUSE_SECONDS = 1.0

# Consecutive refusals before the run gives up.
#
# A site that answers 405 to the first few requests is not having a bad moment,
# it is declining — and the honest response is to stop asking, not to spend the
# whole budget finding out forty times and again in fifteen minutes. One
# degraded stage says more than forty warnings.
REFUSALS_ALLOWED = 3

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
LISTING_URL = re.compile(
    r"/property-to-rent/[^/]+/(?P<slug>[^/]+)/(?P<id>\d+)/?$", re.IGNORECASE
)
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

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

@dataclass(frozen=True)
class Sweep:
    """What one run did.

    `stored` is everything written; `announce` is the part anybody should hear
    about. They differ for a district this source has not been read through yet:
    the sitemap has no dates, so a first pass cannot tell a listing posted an
    hour ago from one posted in June, and announcing it would send a new
    subscriber the whole standing market.
    """

    stored: list[int]
    announce: list[int]

@dataclass(frozen=True)
class Found:
    """What the sitemap alone reveals, before any page is fetched."""

    external_id: str
    url: str
    district: str
    bedrooms: int
    property_type: str | None

def fetch(url: str, *, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")

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
        return district, 0, "studio"

    beds = re.match(r"(\d+)-bed", words)
    if not beds:
        return None

    # Narrowed to four words, and on purpose. The matcher compares the stored
    # type against the filter as an exact string, so "terraced house" would not
    # answer a filter for "house" — and a form offering every phrase the site
    # uses is not a choice anybody can make. The specific word is kept in `raw`.
    for name, kind in (
        ("terraced-house", "house"),
        ("detached-house", "house"),
        ("semi-detached-house", "house"),
        ("bungalow", "house"),
        ("house", "house"),
        ("maisonette", "flat"),
        ("flat", "flat"),
        ("apartment", "flat"),
    ):
        if name in words:
            return district, int(beds.group(1)), kind
    return district, int(beds.group(1)), None

def listings_in(sitemap: str) -> list[Found]:
    found: list[Found] = []
    for url in LOC.findall(sitemap):
        match = LISTING_URL.search(url)
        if not match:
            continue
        read = read_slug(match.group("slug"))
        if read is None:
            continue
        district, beds, kind = read
        found.append(
            Found(
                external_id=match.group("id"),
                url=url,
                district=district,
                bedrooms=beds,
                property_type=kind,
            )
        )
    return found

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

def as_listing(found: Found, html: str) -> Listing | None:
    """A listing, or nothing if the page did not give a price."""

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

    return Listing(
        source_key=SOURCE_KEY,
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
        postcode=postcode,
        postcode_district=found.district,
        # Every OpenRent listing is let by the landlord; that is the site.
        is_landlord_direct=True,
        # The exact phrase from the url, kept because `property_type` is
        # deliberately narrowed to four words for filtering.
        raw={"slug": found.url.rsplit("/", 2)[-2] if "/" in found.url else ""},
    )

def collect(
    conn: Conn,
    run: Run,
    *,
    budget: int = PAGE_BUDGET,
    pause: float = PAUSE_SECONDS,
    dry_run: bool = False,
    get: Any = None,
) -> Sweep:
    """Store every new listing in an enabled district."""

    read = get or fetch
    stored: list[int] = []
    announce: list[int] = []

    with run.stage("scrape", source_key=SOURCE_KEY) as stage:
        if dry_run:
            stage.set("suppressed", True)
            return Sweep([], [])

        wanted = set(store.subscribed_districts(conn))
        stage.set("districts", len(wanted))
        if not wanted:
            # Nobody is waiting for anything, so there is nothing to fetch.
            # Said out loud, because silence here looks identical to a broken
            # scraper — but this is not a fault, it is an empty subscriber list.
            stage.log("info", "no active subscription names a district; nothing to scrape")
            return Sweep([], [])

        index = read(SITEMAP_INDEX)
        children = [u for u in LOC.findall(index) if "listings" in u.lower()]
        stage.set("sitemaps", len(children))

        found: list[Found] = []
        for child in children:
            found.extend(listings_in(read(child)))
        stage.set("in_sitemap", len(found))

        here = [one for one in found if one.district in wanted]
        stage.set("in_our_districts", len(here))

        # No early return when this is empty. A district that genuinely has no
        # OpenRent listings right now still has to be marked as read through,
        # or it would never settle and so would never announce anything once it
        # did get one.

        known = store.known_external_ids(
            conn, source_key=SOURCE_KEY, external_ids=[one.external_id for one in here]
        )
        fresh = [one for one in here if one.external_id not in known]
        stage.count("already_known", len(here) - len(fresh))
        stage.set("new", len(fresh))

        # Which districts we had finished reading *before* this run. Read first,
        # because a district settled below must not retroactively make this
        # run's own backlog announceable.
        settled = store.settled_districts(conn, SOURCE_KEY)
        stage.set("settled_districts", len(settled))

        # A district with nothing new left in it has been read through. From the
        # next run on, anything appearing there appeared after we looked.
        with_fresh = {one.district for one in fresh}
        for district in sorted(wanted - with_fresh - settled):
            store.settle_district(conn, SOURCE_KEY, district)
            stage.count("district_settled")

        refused = 0
        for index_of, one in enumerate(fresh[:budget]):
            if index_of:
                time.sleep(pause)
            try:
                page = read(one.url)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                # One unreachable page is not a reason to abandon a run; a run
                # of them is. Only the first is described, because forty copies
                # of one sentence is not forty pieces of information.
                refused += 1
                if refused == 1:
                    stage.log("warn", f"{one.external_id}: {type(exc).__name__}: {exc}")
                stage.count("unreachable")
                if refused >= REFUSALS_ALLOWED and not stored:
                    stage.degrade(
                        f"openrent refused {refused} requests in a row and gave "
                        f"nothing — stopping this run rather than asking again"
                    )
                    break
                continue
            refused = 0

            listing = as_listing(one, page)
            if listing is None:
                stage.count("no_price")
                continue

            listing_id = store.insert_listing(conn, listing)
            stored.append(listing_id)
            if one.district in settled:
                announce.append(listing_id)
            stage.count("stored")

        stage.count("over_budget", max(0, len(fresh) - budget))
        stage.set("stored", len(stored))
        stage.count("stored_not_announced", len(stored) - len(announce))

    return Sweep(stored, announce)

__all__ = [
    "AGENT", "PAGE_BUDGET", "REFUSALS_ALLOWED", "SOURCE_KEY", "Found", "Sweep",
    "as_listing", "as_text", "collect", "listings_in", "read_slug", "when",
]
