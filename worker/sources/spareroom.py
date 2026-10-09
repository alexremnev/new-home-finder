"""SpareRoom, read from the area pages its own robots.txt leaves open.

The flatshare site, and so the first source here whose stock is mostly *rooms*
rather than whole properties. `property_type` already has a word for that —
see `PROPERTY_TYPES` in apps/web/lib/criteria.ts — so nothing downstream needed
widening; what did need deciding is in `let_of` below.

Measured against the live site on 8 and 9 October 2026. Every number here is
load-bearing and dated, because the shape of this source is unusual enough
that none of it is guessable from the code.

── what robots.txt allows, which is the whole design constraint ─────────────

Read on 9 October 2026. There is no `Sitemap:`, no `Crawl-delay:` and not one
`Allow:` — only disallows, and the `User-agent: *` group is the same list
`ClaudeBot` and `GPTBot` get. Out of bounds:

  * `/flatshare/flatshare_detail.pl` — the legacy listing url;
  * `/flatshare/search.pl?*action=search` — the search form itself;
  * `/flatshare/api.pl` — their own JSON api;
  * `/flatshare/*search_id=` — paged search results, and with them the
    `data-listing-url` on every card, which is a `fad_click.pl?…&search_id=`
    tracker. The card's `<a href>` is the plain url and is what this reader
    follows;
  * **every** search filter as a query parameter — `max_rent=`, `min_beds=`,
    `available_from=`, `bills_inc=`, `landlord=`, `min_term=`, `miles_from_max=`
    and a dozen more;
  * `/flatshare/*sort_by=days_since_placed` — so **newest-first is off
    limits**, which is the single fact that shapes `harvest`.

What is left is the SEO surface, which carries `<meta name="robots"
content="all,index,follow">` and is plain paths with no query string at all:

    /flatshare/london                      the whole city, 10 per page
    /flatshare/london/pageN                page N of it, to page 100
    /flatshare/london/bermondsey           one area
    /flatshare/london/bermondsey/pageN
    /flatshare/london/rotherhithe/16281029 one advert

── there is no outcode page, so this reader is a region reader ──────────────

`/flatshare/london/se16` answers 302 into `search.pl?…&action=search`, which is
disallowed — so an outcode cannot be asked for directly. SpareRoom's crawlable
geography is area *names*, and an area is not an outcode: the Bermondsey page
on 8 October 2026 returned 6 cards in SE1 and 5 in SE16.

The card states the outcode itself, in `data-listing-postcode`, so this reads
the city as one pseudo-district and files each listing where the card says it
is — the arrangement `worker.sources.sweep.Portal.regions` exists for and that
`worker.sources.zoopla.Zoopla.for_region` already uses.

── why the paging rule is a rotating window and not a watermark ─────────────

This is the part that is genuinely different from every other reader here, and
it is worth the paragraphs because the obvious two rules are both wrong.

The default order is SpareRoom's own ranking, not a date, and the upgrades that
drive it are sold by the week: `data-listing-brand` reads `free`, `bold` or
`featured`, and `data-listing-status` reads `boosted` for an advert whose owner
paid to have it lifted. Three measurements on the London feed on 9 October
2026, each of which rules out an otherwise obvious rule:

  * **The dates are spread through the ranking.** Seven of eleven cards on
    every one of pages 1-8 said "new today", and `days-old=0` still held at
    page 45, giving way to `1` by page 60. So today's roughly 500 London
    adverts fill the first fifty-odd pages in no particular order.
  * **The newest ids are scattered.** Listing ids run in sequence on this
    site. In one read, 18456302 sat on page 1, 18456471 — the highest seen
    anywhere — on page 20, and 18455424 on page 40, with ids from months
    earlier beside each of them.
  * **The front of the feed barely moves.** Pages 1-8 were read twice, an hour
    apart: 87 of those 88 cards were the same advert in the same place. One
    new id appeared in 88 slots in an hour, against the fifty-odd adverts
    London posts in that time.

That last one kills both of the rules the other readers use, and the third
measurement is why it is worth stating separately from the first two:

  * *stop when the page reaches the watermark* — `rightmove` and `zoopla` by
    district. There is no date order to reach it in.
  * *stop when every id on the page is one we already have* — what
    `zoopla_london` does. Here it stops on page 1 every time and finds nothing
    ever again, because the front pages are a near-static set of upgraded
    adverts and the new arrivals are behind the pages it never reads.

So this reader takes a fixed slice of the feed each run and **moves the slice
on by one block each time**, cycling through the window where today's stock
lives: `WINDOW` pages of history, `PAGES_PER_RUN` pages a run.

Where the slice sits is a function of the clock and not a stored cursor —
`_offset` below. That is deliberate. A cursor would need a row of its own, a
migration to put it in and a rule for what `purge` does with it, and it would
go stale in exactly the case it is meant to survive: a reader that has been off
for a day resumes mid-window rather than at the front. The clock needs none of
that, reads the same on a fresh database as on an old one, and makes the whole
rule testable by passing `now`.

Nothing is "complete" in the watermark sense and nothing needs to be: a listing
missed on this pass is read on the next cycle, and `listings` is UNIQUE on
(source_key, external_id), so reading one twice costs a page and changes
nothing.

What it costs, at the measured 31KB per page across the wire and on the timer
this reader shares with `zoopla_london`:

    PAGES_PER_RUN = 6        186KB a run
    a full cycle             WINDOW / PAGES_PER_RUN = 10 runs
    weekday, 136 runs        50 minutes to cycle, about 25MB a day
    weekend, 40 runs         about 7MB a day

Against `zoopla_london`'s 7MB a day that is three to four times the traffic,
for a source whose stock the Telegram feed does not carry at all. The latency
is the real price: up to one cycle from posting to alert, so about 25 minutes
median and 50 at worst, not the five minutes the schedule suggests. That is a
property of the site's ordering, not of this code, and the only thing that
would fix it is the sort order robots.txt forbids.

── the detail page, and why it is fetched at all ────────────────────────────

The card is unusually generous: 24 `data-listing-*` attributes, and the price,
the outcode, the availability date, the room descriptor, bills-included, the
advertiser's role and the photograph all come off it. Three filters do not:

    furnished      "Furnishings" in feature--amenities
    pets_allowed   "Pets suitable?" in feature--household-preferences
    min_tenancy    "Minimum term" in feature--availability

so the advert's own page is read for those, through the `enrich` hook, and only
for a listing in a district somebody is actually waiting for. 25KB across the
wire each. It also carries the coordinates, which is what lets
`worker.sources.geo` derive a postcode — SpareRoom states no full postcode
anywhere, on either page.

Two filters have no answer on this site at all: `bathrooms` and
`floor_area_sqft`. Neither is a structured field on either page — bathrooms
appear in prose ("2 bathrooms", "share kitchen & bathrooms") and floor area is
simply absent. Both stay None, which `worker.pipeline.match._range` lets
through, so a filter on either narrows the other sources and does not hide
SpareRoom. Guessing a bathroom count out of the description would put a number
into the duplicate fingerprint on the strength of a sentence, and that is
worse than no number.

── the markup is read as pairs, not as stripped text ────────────────────────

`openrent` strips its detail page to text and finds values by their labels,
because its markup moves. SpareRoom's does not: the facts sit in
`<section class="feature feature--NAME">` blocks holding a `<dl>` of
`<dt class="feature-list__key">` / `<dd class="feature-list__value">` pairs,
and the section name says which group a pair belongs to.

That scoping is not tidiness. The description prose on one live advert read
"Deposit: - Bills: £150 per person per month", so a label search over the whole
page found the word "Deposit" in a sentence before it found the field. Reading
pairs inside a named section cannot make that mistake. It also keeps two
different questions apart: `feature--current-household` asks "Any pets?",
meaning the flatmates' own pets, while `feature--household-preferences` asks
"Pets suitable?", which is the one the filter means.

── this reader never uses the paid proxy ────────────────────────────────────

`direct_only` names the host, so `worker.sources.sweep.collect` pins it to a
direct route whatever `SCRAPE_PROXY` says. Measured from this desk on 8 and 9
October 2026: `Server: Apache` behind Google's load balancer and Fastly, no
Cloudflare, no DataDome, no Akamai, and every page served 200 under
`chrome124`. There is no TLS-fingerprint wall here of the kind that made Zoopla
and OpenRent cost money.

Whether the *server's* address is served is a separate question and untested
from here. If it is refused, the run says so — `refused_this_address` in the
scrape stage, and a warn in the run log — and the answer is to look at that
line and decide, not to quietly start paying for traffic. Hence the pin rather
than the escalation.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from pydantic import ValidationError

from worker.contracts.listing import Furnished, Listing
from worker.obs.log import Stage
from worker.sources.fetch import Fetcher, Refused
from worker.sources.sweep import Catch, Harvest, Memory

SOURCE_KEY = "spareroom"
BASE = "https://www.spareroom.co.uk"

#: The one name this reader sweeps. A pseudo-district — see the module note and
#: `worker.sources.sweep.Portal.regions`.
REGION = "london"

# How far back into the ranking the rotating window reaches, in pages of ten.
#
# Sixty, because `days-old=0` held to page 45 and had given way to `1` by page
# 60 on 9 October 2026: today's London stock is about 500 adverts and fills the
# first fifty-odd pages, and a window that stopped at fifty would leave the
# tail of today outside it for ever.
#
# This is the one number that decides what this reader cannot see. An advert
# whose ranking puts it past page 60 on the day it is posted is never read —
# not late, never — and the only cure is a bigger window, at a proportional
# cost in pages. The feed itself stops at 100: page 100 showed "991-1000 of
# 1000+" and page 120 returned no cards, so 100 is the ceiling on any such
# argument.
WINDOW = 60

# Pages per run, and the only real knob on what this source costs.
#
# Six pages is 186KB a run and a full cycle every ten. At the weekday
# five-minute interval that is a 50-minute cycle; see the module note for what
# the day comes to. Raising it shortens the latency and costs traffic in the
# same proportion, which is the whole of the trade.
PAGES_PER_RUN = 6

# The feed's own ceiling. Measured on 9 October 2026: page 100 showed
# "991-1000 of 1000+" and page 120 returned no cards at all.
LAST_PAGE = 100

# Advert pages per run, a ceiling rather than an expectation.
#
# `enrich` is asked only about a listing that is new to this reader *and* in a
# district somebody subscribes to. A run reads 66 listings out of a London feed
# spanning some 300 outcodes, so with twenty districts subscribed the expected
# number is one or two, and with the three the seed enables it is usually nought.
#
# Fifteen is therefore headroom for an unusual run — a district newly
# subscribed, a cycle that happens to land on a page full of one outcode — and
# a bound on a pathological one: at 25KB a page it caps a run at 375KB, against
# the 186KB the search pages themselves cost.
DETAIL_BUDGET = 15

# The slot the rotating window steps in, in seconds. Five minutes, because that
# is the shortest interval any band of this reader's timer fires at — so in the
# working day each run gets its own slice and consecutive runs do not re-read
# one. The quarter-hourly evening bands stride three slices at a time, which
# still walks the whole window, only in bigger steps and over more runs.
SLOT_SECONDS = 300

# One card, from the opening `<li>` to the next. Split rather than matched:
# the card holds nested `<li>` elements of its own and a non-greedy match to
# `</li>` stops inside the first of them.
CARD_SPLIT = re.compile(r'<li class="listing-result"')

# `data-listing-days-old="2"`, and the other twenty-three.
CARD_ATTR = re.compile(r'data-listing-([a-z-]+)="([^"]*)"')

# The plain listing url, which is the one robots.txt leaves alone. The featured
# card appends `?listing_click=1`; everything after `?` is dropped.
CARD_LINK = re.compile(r'class="listing-card__link"[^>]*href="([^"]+)"')

# "Double room - Available 1st Nov 2026", as rendered. Read as text, because
# the room descriptor and the availability line are separate spans inside it
# and both are wanted.
ROOM_TYPE = re.compile(
    r'<p class="listing-card__room-type">(.*?)</p>', re.DOTALL | re.IGNORECASE
)

# The only statement of bills-included on a search card: a span that is there
# or is not. `feature--extra-cost` on the advert's own page is three-valued and
# overrides this — see `bills_of`.
CARD_BILLS = re.compile(r'listing-card__bills-included', re.IGNORECASE)

# The card's own preview photograph, already resized by their CDN to 400px.
# Protocol-relative, hence `_absolute`.
CARD_IMAGE = re.compile(
    r'<img[^>]*class="[^"]*listing-card__main-image[^"]*"[^>]*src="([^"]+)"',
    re.IGNORECASE,
)
# src may precede class on some cards; both orders are tried.
CARD_IMAGE_REVERSED = re.compile(
    r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*listing-card__main-image[^"]*"',
    re.IGNORECASE,
)

CARD_BLURB = re.compile(
    r'<p class="listing-card__short_description">(.*?)</p>',
    re.DOTALL | re.IGNORECASE,
)

# "Showing 11-20 of 379". Only the total is read, and only to know when a
# shorter area than London has run out.
SHOWING = re.compile(
    r"Showing\s*<strong>\s*[\d,]+-[\d,]+\s*</strong>\s*of\s*<strong>\s*([\d,]+)",
    re.IGNORECASE,
)

# `<section class="feature feature--availability"> … </section>` on an advert's
# own page. Non-greedy to the first `</section>`, which is right here: these
# sections do not nest.
SECTION = re.compile(
    r'<section class="feature feature--([a-z_-]+)"(.*?)</section>',
    re.DOTALL | re.IGNORECASE,
)
PAIR = re.compile(
    r'<dt class="feature-list__key">(.*?)</dt>\s*'
    r'<dd class="feature-list__value">(.*?)</dd>',
    re.DOTALL | re.IGNORECASE,
)

# `location: {latitude: "51.5053699710558",longitude: "-0.0350721620950812",}`
# in the page's own analytics payload. The only coordinates SpareRoom states,
# and the only route to a postcode on this site.
COORDS = re.compile(
    r'latitude:\s*"(-?[\d.]+)"\s*,\s*longitude:\s*"(-?[\d.]+)"', re.IGNORECASE
)

TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)
SPACES = re.compile(r"\s+")

# "£1,500", "£347". The period is a separate attribute on the card and a
# suffix in the `feature--price_room_only` key, so it is not read here.
MONEY = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
PER_WEEK = re.compile(r"\bpw\b|\bp\.?w\b|\bper\s+week\b|\bpcw\b", re.IGNORECASE)

# "21st Dec 2026", "1st Nov 2026". Same shape Zoopla publishes, and read the
# same way; the abbreviation is three letters on every page seen.
WHEN = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})\s+(\d{4})", re.IGNORECASE
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "2 bed flat", "1 bed flat" — a whole property rather than a room in one.
BEDS = re.compile(r"\b(\d+)\s*bed\b", re.IGNORECASE)
# "studio", which is a flat with no separate bedroom. See migration 0042.
STUDIO = re.compile(r"\bstudio\b", re.IGNORECASE)

# A room already gone, stated in the room descriptor rather than in any field:
# `feature--price_room_only` read "£998 pcm (NOW LET)" beside "£998 pcm double"
# on a live advert on 9 October 2026, and the search card for it said
# `status="new"` with `is_now_let` empty. So the words are the only signal.
NOW_LET = re.compile(r"\bnow\s+let\b", re.IGNORECASE)

# What `data-listing-advertiser-role` says, and what it means for
# `landlord_direct_only`. Collected from live pages on 8 and 9 October 2026:
# agent, live out landlord, live in landlord, current flatmate, former flatmate.
#
# Anything else is left None rather than guessed at either way: the filter is
# "not an agency", and a role nobody here has seen is not evidence of that.
AGENT = re.compile(r"\bagent\b|\bagency\b", re.IGNORECASE)
DIRECT = re.compile(
    r"\blandlord\b|\bflatmate\b|\bhousemate\b|\bowner\b|\btenant\b", re.IGNORECASE
)

FURNISHING: tuple[tuple[Furnished, re.Pattern[str]], ...] = (
    ("part", re.compile(r"\bpart[\s-]*furnished\b", re.IGNORECASE)),
    ("unfurnished", re.compile(r"\bunfurnished\b", re.IGNORECASE)),
    ("furnished", re.compile(r"\bfurnished\b", re.IGNORECASE)),
)

OUTCODE = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?$", re.IGNORECASE)


def search_url(region: str = REGION, page: int = 1) -> str:
    """One page of an area's listings.

    Path only, and deliberately: every query parameter this site understands
    for a search is disallowed by robots.txt. See the module note.
    """

    base = f"{BASE}/flatshare/{region.strip('/').lower()}"
    return base if page <= 1 else f"{base}/page{page}"


def listing_url(path: str) -> str | None:
    """A card's own url, absolute and without its click tracker."""

    clean = html.unescape(path).split("?")[0].strip()
    if not clean.startswith("/flatshare/"):
        return None
    return f"{BASE}{clean}"


def as_text(markup: str) -> str:
    """Markup as the words a reader would see."""

    return SPACES.sub(" ", html.unescape(TAGS.sub(" ", markup))).strip()


def _absolute(url: str) -> str | None:
    """A CDN url the alert can actually use.

    SpareRoom serves its card images protocol-relative — `//photos2.…` — and
    neither Telegram nor WhatsApp will fetch one. See worker.ingest.photo for
    the same rule applied to an og:image.
    """

    clean = html.unescape(url).strip()
    if clean.startswith("//"):
        clean = f"https:{clean}"
    return clean[:1000] if clean.startswith("https://") else None


def cards_in(page: str) -> list[str]:
    """Each listing card on a search page, as its own slice of markup."""

    return CARD_SPLIT.split(page)[1:]


def attrs_in(card: str) -> dict[str, str]:
    """The `data-listing-*` attributes, unescaped, without the prefix."""

    return {
        name: html.unescape(value)
        for name, value in CARD_ATTR.findall(card)
    }


def total_in(page: str) -> int | None:
    """How many listings the area holds, as the page itself states it."""

    found = SHOWING.search(page)
    if not found:
        return None
    try:
        return int(found.group(1).replace(",", ""))
    except ValueError:
        return None


def monthly(amount: str, period: str) -> int | None:
    """The rent per calendar month.

    `data-listing-ad-rate-normalised` is **not** a converted figure, whatever
    the name suggests: a card reading `£347 pw` carried `normalised` of
    `£347 pw` too, measured on 8 October 2026. So the weekly rents are
    converted here, by the year, which is what "per calendar month" means.
    """

    found = MONEY.search(html.unescape(amount))
    if not found:
        return None
    try:
        value = float(found.group(1).replace(",", ""))
    except ValueError:
        return None
    if value <= 0:
        return None
    if PER_WEEK.search(period or ""):
        value = value * 52 / 12
    return round(value)


def when(raw: str | None) -> date | None:
    """"21st Dec 2026" as a date, and "now" as today."""

    if not raw:
        return None
    text = raw.strip()
    found = WHEN.search(text)
    if found:
        month = MONTHS.get(found.group(2)[:3].lower())
        if month is None:
            return None
        try:
            return date(int(found.group(3)), month, int(found.group(1)))
        except ValueError:
            return None
    # Checked after the date, because one live card rendered the doubled
    # "Available Available now" and another "- Available 1st Nov 2026"; a
    # "now" test first would have called the second one available today.
    return date.today() if re.search(r"\bnow\b", text, re.IGNORECASE) else None


def first_listed(days_old: str | None, *, today: date | None = None) -> datetime | None:
    """When the advert appeared, from `data-listing-days-old`.

    A whole number of days and nothing finer, so it is stored as the **end** of
    the day it names — the same treatment `zoopla.published` gives a date that
    is only a date, and for the same reason: the end of a day is the latest
    moment the advert could have appeared on it, and a watermark comparison
    must not make something look older than it might be.
    """

    if days_old is None or not days_old.strip().isdigit():
        return None
    day = (today or date.today()) - timedelta(days=int(days_old))
    return datetime.combine(day, time.max, tzinfo=UTC)


def let_of(descriptor: str) -> tuple[str | None, int] | None:
    """What is being let, as `(property_type, bedrooms)`.

    The decision the rest of this module rests on, so it is spelt out.

    A SpareRoom advert is usually a **room in a shared home**, and the filter
    has a word for that: `room`. The card's own `data-listing-property-type`
    says `flat` or `house`, but that is the *building* — storing a room as
    `flat` would mean somebody who ticked Flat and House, and so deliberately
    excluded rooms, was sent nothing but rooms. See the note on
    `PROPERTY_TYPES` in apps/web/lib/criteria.ts.

    So the descriptor decides, not the attribute:

        "Double room", "Single room", "2 doubles"  ->  ("room", 1)
        "1 bed flat", "2 bed flat"                 ->  (None, N)
        "studio"                                   ->  (None, 0)

    A kind of None means "ask the card": its `data-listing-property-type` is
    the only thing that knows a flat from a house, and it is right about the
    building whenever the building is what is being let.

    `bedrooms` is 1 for a room because one room is what is let, whatever the
    advert's headline count. "2 doubles" means the advert has two rooms going,
    not that a tenant gets both; `rooms-in-property` — which ran from 1 to 8 on
    live pages — is the size of the flat and answers no filter anybody sets.

    Returns None for a descriptor with no room left in it. See `NOW_LET`.
    """

    text = as_text(descriptor)
    if not text:
        return None

    beds = BEDS.search(text)
    if beds:
        try:
            count = int(beds.group(1))
        except ValueError:
            return None
        # A whole property, so the card decides the kind.
        return (None, count)
    if STUDIO.search(text):
        return (None, 0)
    return ("room", 1)


def available_in(descriptor: str) -> date | None:
    """The availability date off a search card.

    On the card, not only on the advert's own page: the room-type line reads
    "Double room - Available 1st Nov 2026" or "- Available now", so
    `available_from` — one of the nine filters the web form offers — costs
    nothing. Measured across 41 live cards on 8 and 9 October 2026: every one
    stated one.
    """

    text = as_text(descriptor)
    after = re.search(r"Available\b(.*)$", text, re.IGNORECASE | re.DOTALL)
    return when(after.group(1) if after else None)


def landlord_direct(role: str | None) -> bool | None:
    """Whether the advertiser is the landlord rather than an agency."""

    if not role:
        return None
    if AGENT.search(role):
        return False
    return True if DIRECT.search(role) else None


def picture(card: str) -> str | None:
    """The card's preview photograph, already CDN-resized."""

    for pattern in (CARD_IMAGE, CARD_IMAGE_REVERSED):
        found = pattern.search(card)
        if found:
            absolute = _absolute(found.group(1))
            if absolute:
                return absolute
    return None


def _count(raw: str | None) -> int | None:
    return int(raw) if raw is not None and raw.strip().isdigit() else None


def as_listing(card: str, *, source_key: str = SOURCE_KEY) -> Listing | None:
    """One search card as a `Listing`, or None when it is not usable.

    Nothing here reads the advert's own page: everything below comes off the
    card. `enrich` adds what the card cannot say.
    """

    attrs = attrs_in(card)
    listing_id = (attrs.get("id") or "").strip()
    if not listing_id:
        return None

    # `offered` is a room going; `wanted` is somebody looking for one. The
    # area pages default to offered and every one of 41 live cards said so,
    # but it is checked rather than assumed — a "wanted" card has no rent and
    # would be stored as a listing nobody can rent.
    if (attrs.get("type") or "offered").lower() != "offered":
        return None

    link = CARD_LINK.search(card)
    url = listing_url(link.group(1)) if link else None
    if url is None:
        return None

    district = (attrs.get("postcode") or "").strip().upper()
    if not OUTCODE.match(district):
        # Without an outcode there is nowhere to file it: this reader sweeps
        # the city, so the card is the only thing that says where a listing is.
        return None

    rent = monthly(
        attrs.get("ad-headline-rate") or "",
        attrs.get("ad-headline-rate-period") or "",
    )
    if rent is None:
        return None

    room = ROOM_TYPE.search(card)
    descriptor = room.group(1) if room else ""
    if NOW_LET.search(as_text(descriptor)):
        return None
    let = let_of(descriptor)
    if let is None:
        return None
    kind, bedrooms = let
    if kind is None:
        kind = (attrs.get("property-type") or "").strip().lower() or None

    blurb = CARD_BLURB.search(card)
    return Listing(
        source_key=source_key,
        external_id=listing_id,
        url=url,
        price_pcm=rent,
        bedrooms=bedrooms,
        property_type=kind,
        # Only ever True from a card: the span is there or it is not, and its
        # absence is "not stated" rather than "no". `enrich` can say No.
        bills_included=True if CARD_BILLS.search(card) else None,
        available_from=available_in(descriptor),
        postcode_district=district,
        title=as_text(attrs.get("title") or "") or None,
        description=as_text(blurb.group(1)) if blurb else None,
        is_landlord_direct=landlord_direct(attrs.get("advertiser-role")),
        photo_count=_count(attrs.get("ad-pics")),
        raw=dict(attrs),
    )


# ── the advert's own page ────────────────────────────────────────────────────


def sections_in(page: str) -> dict[str, dict[str, str]]:
    """The advert's facts, as `{section: {label: value}}`.

    Scoped by section on purpose — see the module note on why a label search
    over the whole page reads a value out of the description.
    """

    found: dict[str, dict[str, str]] = {}
    for name, body in SECTION.findall(page):
        pairs = found.setdefault(name.lower(), {})
        for label, value in PAIR.findall(body):
            key = as_text(label).rstrip("?").strip().lower()
            if key:
                pairs[key] = as_text(value)
    return found


def furnished_of(amenities: dict[str, str]) -> Furnished:
    """"Furnishings: Furnished" as one of the four words the filter offers."""

    said = amenities.get("furnishings") or ""
    for word, pattern in FURNISHING:
        if pattern.search(said):
            return word
    return "unknown"


def pets_of(preferences: dict[str, str]) -> bool | None:
    """"Pets suitable?" — the new tenant's pets, not the household's.

    `feature--current-household` asks "Any pets?", which is whether the
    flatmates have one. Reading that as the answer would have told somebody
    with a cat that a flat was fine because *somebody else's* cat lives there.
    """

    return _yes_no(preferences.get("pets suitable"))


def bills_of(cost: dict[str, str]) -> bool | None:
    """"Bills included?" — Yes, No, or Some.

    Three-valued on the page and two-valued in the contract, so "Some" is
    None: the filter means "bills are in the rent", and a listing where some
    of them are is not an answer either way. Seen live on 9 October 2026.
    """

    return _yes_no(cost.get("bills included"))


def _yes_no(said: str | None) -> bool | None:
    if not said:
        return None
    lowered = said.strip().lower()
    if lowered.startswith("yes"):
        return True
    if lowered.startswith("no"):
        return False
    return None


def months_of(availability: dict[str, str]) -> int | None:
    """"Minimum term: 6 months", and None for "None"."""

    said = availability.get("minimum term") or ""
    found = re.search(r"(\d+)\s*month", said, re.IGNORECASE)
    if not found:
        return None
    try:
        months = int(found.group(1))
    except ValueError:
        return None
    return months if 0 <= months <= 120 else None


def deposit_of(cost: dict[str, str]) -> float | None:
    """"Deposit: £450.00". Zero is a real answer and is kept as one."""

    found = MONEY.search(cost.get("deposit") or "")
    if not found:
        return None
    try:
        return float(found.group(1).replace(",", ""))
    except ValueError:
        return None


def coords_in(page: str) -> tuple[float, float] | None:
    """The advert's pin, which is the only route to a postcode on this site."""

    found = COORDS.search(page)
    if not found:
        return None
    try:
        lat, lng = float(found.group(1)), float(found.group(2))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def all_let(page: str) -> bool:
    """Whether every room in the advert has gone.

    An advert can hold several rooms and `feature--price_room_only` lists them
    all: one live page read "£998 pcm (NOW LET)" above "£998 pcm double", so
    one of its two rooms was still going and the advert was still worth
    sending. Only an advert with nothing left is dropped.
    """

    rooms = [
        value
        for name, body in SECTION.findall(page)
        if name.lower().startswith("price")
        for _key, value in PAIR.findall(body)
    ]
    if not rooms:
        return False
    return all(NOW_LET.search(as_text(one)) for one in rooms)


def fill(listing: Listing, page: str) -> Listing | None:
    """The listing with what only its own page states, or None if it is let."""

    if all_let(page):
        return None
    found = sections_in(page)
    amenities = found.get("amenities", {})
    preferences = found.get("household-preferences", {})
    availability = found.get("availability", {})
    cost = found.get("extra-cost", {})

    update: dict[str, Any] = {
        "furnished": furnished_of(amenities),
        "min_tenancy_months": months_of(availability),
        "deposit_pcm": deposit_of(cost),
    }
    pets = pets_of(preferences)
    if pets is not None:
        update["pets_allowed"] = pets
    # The page is three-valued and the card only ever says True, so the page
    # wins where it has an answer at all.
    bills = bills_of(cost)
    if bills is not None:
        update["bills_included"] = bills
    # The page's date beats the card's: same value on every advert compared,
    # and this one is the field rather than a rendered line.
    stated = when(availability.get("available"))
    if stated is not None:
        update["available_from"] = stated
    where = coords_in(page)
    if where is not None:
        update["lat"], update["lng"] = where
    return listing.model_copy(update=update)


# ── the reader ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Read:
    """One search page, as far as this module is concerned."""

    caught: list[Catch]
    total: int | None = None
    skipped: int = 0
    invalid: int = 0

    @property
    def cards(self) -> int:
        """How many cards the page held, usable or not.

        Nought is the only answer `harvest` acts on, and it means the page is
        past the end of the area — or that the markup has moved under us. The
        arithmetic rather than a second split of the body: every card is
        counted exactly once, as caught, skipped or invalid.
        """

        return len(self.caught) + self.skipped + self.invalid


def catches_in(page: str) -> Read:
    """Every usable listing on one search page."""

    cards = cards_in(page)
    caught: list[Catch] = []
    invalid = 0
    for card in cards:
        try:
            listing = as_listing(card)
        except ValidationError:
            # Never fatal — the same guard as openrent, rightmove and zoopla.
            invalid += 1
            continue
        if listing is None:
            continue
        attrs = attrs_in(card)
        caught.append(
            Catch(
                listing=listing,
                image=picture(card),
                first_listed=first_listed(attrs.get("days-old")),
                # `featured` is the one card in a slot of its own, under a
                # "Featured" header. `bold` and `boosted` are upgrades too, but
                # ten of eleven cards on a page carry one, so calling those
                # promoted would say nothing. Nothing here pages on the sort
                # order anyway — see the module note.
                promoted=(attrs.get("brand") or "").lower() == "featured",
            )
        )
    return Read(
        caught=caught,
        total=total_in(page),
        skipped=len(cards) - len(caught) - invalid,
        invalid=invalid,
    )


@dataclass
class SpareRoom:
    """The portal, as `worker.sources.sweep.collect` needs it.

    Not frozen: `_details` is this run's remaining detail-page budget and
    `enrich` spends it, the same arrangement `openrent.OpenRent` uses.
    """

    key: str = SOURCE_KEY
    #: `days-old` is a day the portal stands behind, so announceability is
    #: decided from it. Stored as the end of its day — see `first_listed`.
    dated: bool = True

    #: Swept instead of the subscribed districts. The card files each listing
    #: itself; see the module note.
    regions: tuple[str, ...] = (REGION,)

    #: Never through the paid proxy, whatever SCRAPE_PROXY says. See the
    #: module note and `worker.sources.sweep.collect`.
    direct_only: tuple[str, ...] = ("spareroom.co.uk",)

    pages_per_run: int = PAGES_PER_RUN
    window: int = WINDOW
    detail_budget: int = DETAIL_BUDGET

    _details: int = field(default=-1, init=False)

    def enrich(self, catch: Catch, get: Fetcher, stage: Stage) -> Catch:
        """The three filters a search card cannot answer, from the advert.

        Called by the engine only for a listing in a district somebody is
        waiting for, so this scales with the subscriptions rather than with
        London. Returns the catch unchanged on anything unexpected: a listing
        with no furnishing stated is still a listing, and
        `worker.pipeline.match` lets a missing value through.
        """

        if self._details < 0:
            self._details = self.detail_budget
        if self._details <= 0:
            stage.count("detail_over_budget")
            return catch
        self._details -= 1
        try:
            reply = get.get(catch.listing.url)
        except (Refused, OSError) as error:
            stage.count("detail_unreachable")
            stage.log(
                "info",
                f"{catch.listing.external_id}: could not read the advert — "
                f"{type(error).__name__}",
            )
            return catch
        try:
            filled = fill(catch.listing, reply.body)
        except ValidationError:
            # The card already validated, so this is the page contradicting
            # it. Keeping the card's version is the conservative answer.
            stage.count("detail_invalid")
            return catch
        if filled is None:
            stage.count("now_let")
            return catch
        stage.count("detail_pages")
        return replace(catch, listing=filled)

    def harvest(
        self,
        district: str,
        get: Fetcher,
        stage: Stage,
        since: datetime | None,
        memory: Memory | None = None,
        *,
        now: datetime | None = None,
    ) -> Harvest:
        """`PAGES_PER_RUN` pages of the feed, moving on one slice per run.

        `since` is read for one thing only — whether this is the first run on
        this name, which costs one page and announces nothing. Everything else
        a watermark is for is useless here: the feed is not in date order. See
        the module note.

        `memory` is accepted and unused. The engine passes it to every portal,
        and the two questions it answers — which ids are already stored, and
        which were resolved elsewhere — are both about deciding what to fetch
        next. This reader's slice is decided by the clock, and `_collect` tells
        new from known for itself after the harvest.
        """

        del memory
        caught: list[Catch] = []
        seen: set[str] = set()
        pages = 0
        start = self._offset(now)

        # One page on the first run of a name. Nothing on it can be announced
        # — `_announceable` refuses everything until the watch is settled — so
        # reading six pages to throw five away is five pages wasted.
        wanted = 1 if since is None else self.pages_per_run
        for nth in range(wanted):
            try:
                reply = get.get(search_url(district, self._page(start + nth)))
            except Refused:
                # Raised on to the engine rather than swallowed: it counts
                # refusals, decides when to stop asking and degrades a run that
                # read nothing at all. A refusal part-way through has still
                # read something, so that one only ends the slice.
                if pages:
                    break
                raise
            pages += 1
            read = catches_in(reply.body)
            if read.skipped:
                stage.count("not_a_listing", read.skipped)
            if read.invalid:
                stage.count("invalid", read.invalid)
            if nth == 0 and read.total is not None:
                # How much stock the feed claims. Not used to decide anything —
                # London answers "1000+", which is its ceiling rather than a
                # count — but it is the number that would show the window had
                # been set against the wrong size of feed.
                stage.set("feed_total", read.total)
            if not read.cards:
                # Past the end of a shorter area than London, or a page shape
                # this reader no longer recognises. Counted either way, because
                # the two look identical from here and the second is the one
                # worth noticing in the admin panel.
                stage.count("empty_page")
                break
            for one in read.caught:
                if one.listing.external_id not in seen:
                    seen.add(one.listing.external_id)
                    caught.append(one)

        stage.set("window_from", self._page(start))
        stage.set("window_pages", pages)
        # Always complete, and that is a statement rather than an oversight.
        # "Complete" in the engine means "read back to the watermark", and this
        # reader never claims to have: it reads a slice of a ranking. Saying
        # otherwise would make `_collect` degrade the stage on every single run
        # and never move `swept_at` — which `stale_watches` then reads as a
        # permanent gap, voids the watch, and so never announces anything. That
        # is exactly the trap openrent fell into; see the note on `mark_swept`.
        return Harvest(caught=caught, complete=True, pages=pages)

    def _page(self, offset: int) -> int:
        """A page number inside the window, 1-based and wrapping."""

        return 1 + (offset % min(self.window, LAST_PAGE))

    def _offset(self, now: datetime | None = None) -> int:
        """Where this run's slice starts, from the clock. See the module note."""

        at = now or datetime.now(UTC)
        return (int(at.timestamp()) // SLOT_SECONDS) * self.pages_per_run


__all__ = [
    "BASE",
    "DETAIL_BUDGET",
    "LAST_PAGE",
    "PAGES_PER_RUN",
    "REGION",
    "SOURCE_KEY",
    "WINDOW",
    "Read",
    "SpareRoom",
    "all_let",
    "as_listing",
    "as_text",
    "attrs_in",
    "available_in",
    "bills_of",
    "cards_in",
    "catches_in",
    "coords_in",
    "deposit_of",
    "fill",
    "first_listed",
    "furnished_of",
    "landlord_direct",
    "let_of",
    "listing_url",
    "monthly",
    "months_of",
    "pets_of",
    "picture",
    "search_url",
    "sections_in",
    "total_in",
    "when",
]
