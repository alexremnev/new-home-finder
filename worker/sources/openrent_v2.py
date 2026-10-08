"""OpenRent, read from its search pages instead of its sitemap.

A second OpenRent reader, beside `worker.sources.openrent` rather than
replacing it. The original is left exactly as it is: it works, and the two can
be run side by side and compared before anything is switched off.

── why there is a second one ────────────────────────────────────────────────

The original discovers listings from the nationwide sitemap, because that is
the only thing `robots.txt` pointed at. That sitemap carries no `lastmod`, no
`ETag` and no `Last-Modified`, ignores `Range`, and is served uncompressed
whatever `Accept-Encoding` asks for — all measured. So every run downloads the
whole of the United Kingdom to find out what changed in E14, which came to
about 950MB a day to discover two or three listings.

The search pages make that unnecessary. `robots.txt`, re-read on 26 September
2026, does not disallow `/properties-to-rent/`, and one district page carries:

  * `var PROPERTYIDS = [ 2937375, 3030969, … ]` — **every** listing id the
    search matches, not only the twenty rendered. E14 returned 328 ids in one
    92KB page, with `NUMBEROFPROPERTIES = 328` beside it to check against.
  * the first twenty as full cards: the slug, the monthly rent, and a
    CDN-resized photograph.

So discovery costs one request per district instead of the whole sitemap, and
it scales with the districts somebody actually subscribes to rather than with
the size of the country.

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
count and the property type, which is why `read_slug` is imported from the
original reader rather than rewritten here.

── what still needs the listing page ───────────────────────────────────────

The rent. The cards carry it for the twenty that are rendered, but a district
has hundreds, so the page is still fetched for each genuinely new listing in a
subscribed district — exactly as the original does, and only for those. The
deposit, the minimum tenancy, the full postcode and the availability date come
with it, and `as_listing` from the original parses all of it.

── why this one is undated ─────────────────────────────────────────────────

OpenRent publishes no listing date anywhere this reader can see it. The search
has a "New" sort in its dropdown, but the value does not take as a query
parameter — tried 0 through 6, and the order never changed — and the cards say
only "Last updated around 2 weeks ago". So there is no watermark to compare
against, and this source uses the engine's read-through rule: a district is
read silently until a run finds nothing new left in it, and only then does it
start announcing. See worker.sources.sweep.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from worker.obs.log import Stage
from worker.sources.fetch import BLOCKED as REFUSALS
from worker.sources.fetch import Fetcher, Refused
from worker.sources.openrent import as_listing, read_slug
from worker.sources.sweep import Catch, Harvest, Memory

SOURCE_KEY = "openrent_v2"
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
class OpenRentV2:
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
                # A slug shape the original reader does not recognise. Counted
                # rather than guessed at, and remembered so we do not ask about
                # this id again.
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
                # This reader's own key, not the parser's. See 0053.
                listing = as_listing(found, detail.body, source_key=self.key)
            except ValidationError as error:
                # Never fatal — the same guard as the original reader, for the
                # same reason: one listing the contract refuses cost a whole
                # run once already.
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


def _found(listing_id: str, url: str, district: str, read: tuple[str, int, str | None]) -> Any:
    """The original reader's `Found`, built from what the slug gave us."""

    from worker.sources.openrent import Found

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

    from worker.ingest.photo import image_in

    return image_in(page)


__all__ = [
    "BASE", "DETAIL_BUDGET", "PAUSE_SECONDS", "SLUG_BUDGET", "SOURCE_KEY",
    "OpenRentV2", "how_many", "ids_in", "images_in", "paths_in",
    "search_url", "short_url", "slug_from_path", "slugs_in",
]
