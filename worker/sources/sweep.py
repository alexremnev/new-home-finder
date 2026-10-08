"""The part of scraping that is the same for every portal.

Three portals, three ways of finding a listing, and one identical job once it
is found: is it new, is it a copy of one already sent, store it, decide whether
anybody should hear about it. That second half is here, so that adding Zoopla
is a matter of describing Zoopla rather than writing all of this again.

── what a portal has to provide ─────────────────────────────────────────────

`harvest(district, get, stage)` — what is currently advertised in one district,
as `Catch` records, newest first. How it gets them is its own business:
Rightmove reads a search page's embedded JSON, Zoopla will do much the same,
OpenRent walks a nationwide sitemap and then fetches a page each.

And `dated` — whether the portal says when each listing first appeared. It
decides which of the two rules below applies, and they are very different in
cost.

── the first run problem, and the two answers to it ─────────────────────────

The first time a district is read, everything in it is new to us and almost
none of it is new to the market. Announcing that first pass sends a new
subscriber every flat that has been standing on the portal since June.

A portal with no dates (OpenRent, whose sitemap carries no `lastmod`) can only
answer this by exhaustion: read the district silently until a run finds nothing
new left in it, and only then start announcing. That costs a full read of the
district before the subscriber hears anything.

A portal that publishes a first-seen date per listing (Rightmove's
`firstVisibleDate`) needs none of that. Note the moment we started watching the
district, and announce exactly those listings that first appeared after it.
That is not an approximation of the exhaustion rule, it is the thing the
exhaustion rule was trying to estimate — and it costs one page, because the
listings are sorted newest first and we can stop as soon as we are behind the
watermark.

Both rules write to `source_sweeps`. For an undated portal a row there means
"read through"; for a dated one it means "watching since", and the timestamp is
load-bearing rather than incidental.

── the byte count ───────────────────────────────────────────────────────────

`bytes` is the run's transfer, from libcurl's own counter, because residential
proxy traffic is billed and an estimate is not good enough to check an invoice
against. `proxy_bytes` is the billable subset — what actually went through the
proxy, as opposed to what went straight out from this machine.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import psycopg

from worker import store
from worker.contracts.listing import Listing
from worker.obs import Run
from worker.obs.log import Stage
from worker.sources import geo
from worker.sources.fetch import Fetcher, Refused

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

# Districts per run. A first run against a long subscriber list must not try to
# read the whole of London before the timer fires again.
DISTRICT_BUDGET = 25

# Between districts. No portal here publishes a crawl delay, so this is manners
# rather than obedience — and cheap, at a request or two per district.
PAUSE_SECONDS = 1.0

# Listing pages fetched per run to fill in a missing postcode. Only for
# listings we are about to store, so in the steady state this is a handful.
#
# It costs about 47KB on Zoopla and 100KB on Rightmove, and it buys two
# things: an alert that names the street rather than two square miles, and a
# duplicate rule that works at all — `store.mark_duplicate` compares on the
# full postcode and skips any listing without one, so the same flat on both
# portals was being sent twice. See worker.sources.postcode.
POSTCODE_BUDGET = 40

# A district unread for this long has a gap we cannot account for, so its
# watch is void — see the note in `collect`. Must exceed the schedule's own
# overnight pause: nine hours on a weekday, fourteen at the weekend.
GAP_HOURS = 24

# Consecutive refusals before a run gives up. A portal that has declined three
# times running is not having a bad moment, and the honest answer is to stop
# asking rather than to spend the whole budget finding out.
REFUSALS_ALLOWED = 3


@dataclass(frozen=True)
class Catch:
    """One listing as a portal found it, with its preview picture."""

    listing: Listing

    #: A url, never bytes. Telegram and WhatsApp fetch the picture themselves
    #: from their own servers when the alert goes out, so it never crosses our
    #: connection: no proxy traffic, and nothing for us to re-encode. Portals
    #: that publish a pre-resized CDN variant hand us the small one for free.
    image: str | None = None

    #: When the portal says this listing first appeared. The whole of the
    #: dated rule rests on it, so a portal that cannot say leaves it None and
    #: is treated as undated for that listing.
    first_listed: datetime | None = None

    #: The value the portal's own newest-first ordering is actually based on,
    #: when that is not the same thing as `first_listed`. Rightmove's "most
    #: recent" sorts on the later of added-or-reduced, so a flat listed in July
    #: and reduced today sits near the top with a July `first_listed`. Paging
    #: has to follow the order the page is in, and announcing has to follow
    #: when the flat actually appeared; conflating them either stops paging
    #: early or announces a price cut as a new listing. None means the portal
    #: sorts by `first_listed` and the two are the same.
    ordered_by: datetime | None = None

    @property
    def sort_key(self) -> datetime | None:
        """Where this listing sits in the portal's own ordering."""

        return self.ordered_by or self.first_listed

    #: Promoted into a position it did not earn — Rightmove's featured and
    #: premium slots put a listing from last May at the top of a newest-first
    #: page. Stored and announced like any other, but never used to decide
    #: whether to stop paging, because it says nothing about where we are in
    #: the sort order.
    promoted: bool = False


@dataclass(frozen=True)
class Harvest:
    """What one district gave up, newest first."""

    caught: list[Catch]
    #: False when the portal could not show us everything we asked for — a
    #: refusal part-way, or a page cap reached while listings were still newer
    #: than the watermark. An undated portal must never settle on a partial
    #: view; for a dated portal this is only ever a counter.
    complete: bool = True
    pages: int = 1


@dataclass(frozen=True)
class Memory:
    """What the engine can tell a portal about what we already have.

    One object rather than a callback per question, because a portal whose
    discovery is "ids first, details later" needs several and the list will
    grow: OpenRent publishes every id in a district on one page, and deciding
    what to fetch means knowing which of them are already stored, which have
    already been resolved to somewhere else, and then writing down what this
    pass learned.
    """

    #: Which of these external ids are already in `listings` for this source.
    stored: Callable[[list[str]], set[str]]

    #: Which have already been resolved, and to which district — including the
    #: ones deliberately not stored because they turned out to belong to a
    #: neighbouring district. See 0053: without this, a radius search
    #: re-resolves a third of its results on every run, for ever.
    resolved: Callable[[list[str]], dict[str, str | None]]

    #: Write down what this pass resolved. District None means the slug could
    #: not be read, which is still worth not asking about twice.
    remember: Callable[[dict[str, str | None]], None]


class Portal(Protocol):
    """What `collect` needs to know about a site."""

    key: str
    #: True when every listing carries a trustworthy first-appeared date.
    dated: bool

    #: Names to sweep instead of the subscribed districts, or empty for the
    #: usual district-by-district read.
    #:
    #: A reader that can search a whole region in one query returns the region
    #: here, and the engine treats it as a pseudo-district: one watch, one
    #: watermark, one gap rule, all the existing machinery unchanged. What it
    #: cannot do is tell the engine which district a listing is in — so a
    #: portal with regions must file each listing itself, and `collect` trusts
    #: `listing.postcode_district` rather than the name it searched for.
    #:
    #: Why it is worth it: a district sweep costs one page per subscribed
    #: district per run, for ever, and a region sweep costs one page per run
    #: whatever anybody subscribes to. Measured on two weeks of this project's
    #: own counters: 4.8MB per district per day against about 9MB a day for
    #: every district in London. See worker.sources.zoopla.REGION.
    regions: tuple[str, ...]

    def postcode_for(
        self, catch: Catch, get: Fetcher, stage: Stage
    ) -> str | None:
        """The full postcode for a listing whose search page withheld it.

        Optional: a portal that always states one — or that has nowhere to
        look — returns None and costs nothing. See worker.sources.postcode for
        why this is worth a request per stored listing.
        """

    def harvest(
        self,
        district: str,
        get: Fetcher,
        stage: Stage,
        since: datetime | None,
        memory: Memory,
    ) -> Harvest: ...


@dataclass(frozen=True)
class Sweep:
    """What one run did.

    `stored` is everything written; `announce` is the part anybody should hear
    about. The two differ for a district we have only just started watching.
    """

    stored: list[int] = field(default_factory=list)
    announce: list[int] = field(default_factory=list)
    wire: int = 0
    proxy_wire: int = 0


@dataclass(frozen=True)
class Kept:
    """What storing one listing came to.

    The id is given even for a copy, and on purpose: the reader did see that
    flat, and the sighting record is about who saw what rather than about what
    gets sent. Returning None for a copy — which is what this did first — lost
    the id and quietly left those listings out of the feed-versus-scraper
    comparison altogether.
    """

    listing_id: int
    #: A copy of one already stored from another portal, so nobody is told
    #: about it. See `store.mark_duplicate`.
    duplicate: bool


def stale_watches(
    wanted: list[str],
    watching: dict[str, store.Watch],
    *,
    now: datetime | None = None,
    hours: int = GAP_HOURS,
) -> list[str]:
    """Districts whose watch has a hole in it, and so cannot be trusted.

    `settled_at` means "everything after this moment, we saw". That is only
    true while we kept looking. A district drops out of the wanted list the
    moment it leaves the last subscription naming it — the list is read fresh
    every run — and if somebody adds it back a week later the marks still
    claim a week of coverage that never happened. Every listing published in
    the gap then looks like news, and the subscriber gets a week of it at once.

    The same hole opens without anybody touching a filter: a machine asleep for
    a day, a portal refusing us for two, a district starved by the run budget.

    The threshold has to clear the schedule's own overnight pause — nine hours
    on a weekday, fourteen at the weekend — so a day is comfortably past both
    and still short enough that the flood never reaches anybody.
    """

    cutoff = (now or datetime.now(UTC)) - timedelta(hours=hours)
    return [
        one
        for one in sorted(wanted)
        if one in watching
        and (watching[one].swept_at is None or watching[one].swept_at < cutoff)
    ]


def sweep_order(
    wanted: list[str], watching: dict[str, store.Watch]
) -> list[str]:
    """Which districts to read first.

    Unwatched ones lead: they are the ones a waiting subscriber needs read
    before they hear anything at all, and on a budget that matters more than an
    even sweep.

    After them, least recently read — NOT alphabetically. With more subscribed
    districts than the budget, sorting by name meant the same first
    twenty-five were read every run and everything past them was never read at
    all. Which, now that an unread district has its watch voided, would also
    have left them permanently restarting.
    """

    far_past = datetime.min.replace(tzinfo=UTC)
    return sorted(
        wanted,
        key=lambda one: (
            one in watching,
            (watching[one].swept_at or far_past) if one in watching else far_past,
            one,
        ),
    )


def _derive_postcodes(fresh: list[Catch], stage: Stage) -> dict[str, str]:
    """external_id -> postcode, worked out from coordinates. See 0056.

    Only for listings that still have none and have somewhere to look. The
    district they are already filed under is passed as well and the answer is
    refused if its outward code disagrees — a neighbour's postcode in an alert
    reads as fact and is worse than the district alone.
    """

    asks = [
        geo.Ask(
            key=one.listing.external_id,
            lat=one.listing.lat,
            lng=one.listing.lng,
            district=one.listing.postcode_district,
        )
        for one in fresh
        if one.listing.postcode is None
        and one.listing.lat is not None
        and one.listing.lng is not None
    ]
    if not asks:
        return {}

    found = geo.nearest(asks)
    stage.count("postcode_asked", len(asks))
    if len(found) < len(asks):
        # Not a fault: no centroid within range, or one in the wrong district,
        # or a request that failed. All three leave the listing as it was.
        stage.count("postcode_not_derived", len(asks) - len(found))
    return found


def _fill_postcode(
    portal: Portal, catch: Catch, get: Fetcher, stage: Stage
) -> Catch:
    """The same catch with its postcode filled in, if the portal could say.

    Returns the catch unchanged when it could not — which is the common case
    for a portal with no detail page worth reading, and the honest answer when
    the page was ambiguous. A guessed postcode would go into the alert and into
    the duplicate fingerprint, so a wrong one is worse than none.
    """

    ask = getattr(portal, "postcode_for", None)
    if ask is None:
        return catch
    try:
        found = ask(catch, get, stage)
    except (Refused, OSError) as error:
        # One unreadable page is not worth losing the listing over: it is
        # stored with the outward code, exactly as before this existed.
        stage.count("postcode_unreachable")
        stage.log(
            "info",
            f"{catch.listing.external_id}: could not read a postcode — "
            f"{type(error).__name__}",
        )
        return catch
    if not found:
        return catch
    return replace(catch, listing=catch.listing.model_copy(update={"postcode": found}))


def keep(conn: Conn, stage: Stage, catch: Catch) -> Kept:
    """Store one listing.

    The half of scraping that has nothing to do with which portal it came
    from, so every portal gets the duplicate rule and the picture without
    having to remember to.
    """

    listing_id = store.insert_listing(conn, catch.listing)

    # The same flat advertised by an agent on two portals is one flat, and
    # whichever we saw first is the one that gets sent.
    if store.mark_duplicate(conn, listing_id) is not None:
        stage.count("duplicate")
        return Kept(listing_id=listing_id, duplicate=True)

    store.set_listing_image(conn, listing_id, catch.image)
    stage.count("with_photo" if catch.image else "no_photo")
    return Kept(listing_id=listing_id, duplicate=False)


def collect(
    conn: Conn,
    run: Run,
    portal: Portal,
    *,
    budget: int = DISTRICT_BUDGET,
    pause: float = PAUSE_SECONDS,
    dry_run: bool = False,
    get: Fetcher | None = None,
) -> Sweep:
    """Read every subscribed district on one portal and store what is new."""

    fetcher = get or Fetcher.from_env()
    # Ours to close if we made it; the caller's to keep if they passed one.
    mine = get is None
    stored: list[int] = []
    announce: list[int] = []

    try:
        return _collect(
            conn, run, portal, fetcher,
            budget=budget, pause=pause, dry_run=dry_run,
            stored=stored, announce=announce,
        )
    finally:
        if mine:
            fetcher.close()


def _collect(
    conn: Conn,
    run: Run,
    portal: Portal,
    fetcher: Fetcher,
    *,
    budget: int,
    pause: float,
    dry_run: bool,
    stored: list[int],
    announce: list[int],
) -> Sweep:

    with run.stage("scrape", source_key=portal.key) as stage:
        stage.set("proxied", bool(fetcher.proxy))
        if dry_run:
            stage.set("suppressed", True)
            return Sweep()

        wanted = sorted(store.subscribed_districts(conn))
        stage.set("districts", len(wanted))
        if not wanted:
            # Said out loud: silence here looks exactly like a broken scraper,
            # but this is an empty subscriber list, not a fault.
            stage.log(
                "info", "no active subscription names a district; nothing to scrape"
            )
            return Sweep()

        # What gets read, which is no longer the same thing as what is wanted.
        # A region reader sweeps one name and sorts the results out afterwards;
        # a district reader sweeps exactly the districts somebody asked for.
        regions = tuple(getattr(portal, "regions", ()) or ())
        to_read = sorted(regions) if regions else wanted
        wanted_set = set(wanted)
        if regions:
            stage.set("regions", list(regions))

        # Read up front, before anything below writes to it: a district that
        # starts being watched during this run must not retroactively make this
        # run's own backlog announceable.
        # Two timestamps per district, and they are not interchangeable.
        # `settled_at` is when watching began and decides what counts as news;
        # `swept_at` is when we last read it and decides how far back to page.
        # Using the first for both is what made a district watched since August
        # page back through August on every run. See 0050.
        watching = store.district_watch(conn, portal.key)
        stage.set("settled_districts", len(watching))

        stale = stale_watches(to_read, watching)
        if stale:
            store.void_watch(conn, portal.key, stale)
            stage.set("watch_restarted", stale)
            stage.log(
                "warn",
                f"{', '.join(stale)} had not been read for over {GAP_HOURS}h, so "
                f"what was missed in between cannot be told from what is new; "
                f"starting those watches again, which costs one silent pass",
            )
            # Not `one`: the same name is the Catch in the loop below, and
            # binding it to a district string here left the type checker
            # reading every `one.listing` down there as an attribute on a str.
            for gone in stale:
                del watching[gone]

        order = sweep_order(to_read, watching)

        refused = 0
        postcodes = POSTCODE_BUDGET
        for nth, district in enumerate(order[:budget]):
            if nth:
                time.sleep(pause)
            watch = watching.get(district)
            # What the portal is asked to read back to.
            read_since = watch.swept_at if watch else None
            # What decides whether any of it is worth telling anybody.
            news_since = watch.settled_at if watch else None
            def already(ids: list[str]) -> set[str]:
                return store.known_external_ids(
                    conn, source_key=portal.key, external_ids=ids
                )

            memory = Memory(
                stored=already,
                resolved=lambda ids: store.seen_ids(
                    conn, source_key=portal.key, external_ids=ids
                ),
                remember=lambda learned: store.remember_seen(
                    conn, portal.key, learned
                ),
            )

            # A dated portal is asked to read back to when we last swept; an
            # undated one gets the date watching began, because its own rule is
            # "has this district been read through at all". Handing it
            # `swept_at` would tell it nothing it can use.
            try:
                harvest = portal.harvest(
                    district,
                    fetcher,
                    stage,
                    read_since if portal.dated else news_since,
                    memory,
                )
            except Refused as exc:
                refused += 1
                # Only the first is described. Twenty-five copies of one
                # sentence is not twenty-five pieces of information.
                if refused == 1:
                    stage.log("warn", f"{district}: {exc}")
                stage.count("refused")
                if refused >= REFUSALS_ALLOWED and not stored:
                    stage.degrade(
                        f"{portal.key} refused {refused} requests in a row and "
                        f"gave nothing — stopping this run rather than asking again"
                    )
                    break
                continue
            refused = 0
            stage.count("pages", harvest.pages)

            ids = [one.listing.external_id for one in harvest.caught]
            # id-carrying, because both answers are wanted at once: which of
            # these are new to us, and which row to record a sighting against
            # for the ones that are not.
            have = store.listing_ids_for(
                conn, source_key=portal.key, external_ids=ids
            )
            known = set(have)
            fresh = [
                one for one in harvest.caught if one.listing.external_id not in known
            ]
            stage.count("seen", len(harvest.caught))
            stage.count("already_known", len(harvest.caught) - len(fresh))
            stage.count("new", len(fresh))

            # One request for the whole district, before the loop, because
            # the loop stores as it goes and a postcode arriving afterwards
            # would arrive after the duplicate rule had already run without
            # it. Free, so it is asked for every listing that still has no
            # postcode — including the ones past the page budget below, which
            # is the shortfall it exists to cover.
            derived = _derive_postcodes(fresh, stage)

            for one in fresh:
                # A listing page costs 43KB and only earns it for a district
                # somebody is waiting for. The search page, by contrast, is
                # already paid for — so everything on it is stored either way.
                #
                # Storing a district nobody has chosen is close to free and buys
                # something real: when somebody does choose it, the listings are
                # already there and `backfill_from` decides what they hear
                # about, instead of the district starting cold. In district mode
                # this is always true, because the only districts read are the
                # wanted ones.
                worth_a_page = one.listing.postcode_district in wanted_set
                if not worth_a_page:
                    stage.count("outside_the_filter")

                # Before storing, not after: the postcode is what the
                # duplicate rule compares on, and filling it in afterwards
                # would mean the comparison had already been made without it.
                if one.listing.postcode is None and postcodes > 0 and worth_a_page:
                    filled = _fill_postcode(portal, one, fetcher, stage)
                    if filled is not one:
                        one = filled
                        stage.count("postcode_found")
                    else:
                        stage.count("postcode_missing")
                    postcodes -= 1

                # The portal's own answer first, always: this one is the
                # nearest centroid to a rounded coordinate, and it is here to
                # beat NULL rather than to beat the page.
                guess = derived.get(one.listing.external_id)
                if one.listing.postcode is None and guess is not None:
                    one = replace(one, listing=one.listing.model_copy(
                        update={"postcode": guess, "postcode_source": "derived"}
                    ))
                    stage.count("postcode_derived")

                kept = keep(conn, stage, one)
                # Recorded either way, so that a copy still counts as seen.
                have[one.listing.external_id] = kept.listing_id
                if kept.duplicate:
                    continue
                stored.append(kept.listing_id)
                stage.count("stored")
                if _announceable(portal, one, news_since):
                    announce.append(kept.listing_id)

            # That this reader saw them — all of them, including the ones some
            # other reader stored first. Skipping those would make a scraper
            # look as though it had missed everything the Telegram feed got in
            # ahead of it, which is the opposite of what this records. One
            # statement for the district; see 0052.
            store.record_sightings(conn, list(have.values()), portal.key)

            if portal.dated:
                # Start watching, from now. Everything on the portal at this
                # moment predates the watch and so is never announced, which is
                # the whole point — and it takes one page rather than a full
                # read of the district to establish.
                if news_since is None:
                    store.settle_district(conn, portal.key, district)
                    stage.count("district_watched")
            elif harvest.complete and not fresh and news_since is None:
                # Nothing new left, and we saw all of it: read through. From
                # the next run on, anything appearing here appeared after we
                # looked.
                store.settle_district(conn, portal.key, district)
                stage.count("district_settled")

            # For every portal, dated or not, and only on a complete read: a
            # run that stopped at the page cap has not seen everything since
            # the last sweep, and moving the mark would leave that gap unread
            # for ever.
            #
            # This used to sit inside the `dated` branch, so an undated source
            # never recorded a sweep at all. `swept_at` then stayed NULL, which
            # `stale_watches` reads as a gap — so openrent_v2 had its watch
            # voided on every single run and announced nothing, which is
            # precisely the fault this rule exists to prevent.
            if harvest.complete:
                store.mark_swept(conn, portal.key, district)
            if not harvest.complete:
                stage.count("district_partial")
                if regions:
                    # The watermark stays where it is, so the next run tries
                    # again — but a region search can only reach its newest
                    # 1,000 results (pageNumberMax 40, measured), so a gap this
                    # reader cannot close in one run is a gap that will outlive
                    # the pages that cover it. That is a fault, not a counter.
                    stage.degrade(
                        f"{district} did not read back to the watermark in "
                        f"{harvest.pages} pages; listings published in the gap "
                        f"may fall past the search's own 1,000-result ceiling"
                    )

        stage.count("over_budget", max(0, len(order) - budget))
        stage.set("requests", fetcher.requests)
        # The transfer, not the decompressed body — see the module note. Named
        # `bytes` because that is the counter the admin System tab charts.
        stage.set("bytes", fetcher.wire)
        stage.set("proxy_requests", fetcher.proxied)
        if fetcher.blocked:
            # Worth saying out loud even when the proxy then carried the run:
            # this is the portal telling us it does not want this address, and
            # the difference between "we are paying for traffic" and "we are
            # paying for traffic because we have to" is this line.
            stage.set("refused_this_address", sorted(fetcher.blocked))
            stage.log(
                "info" if fetcher.proxy else "warn",
                f"{', '.join(sorted(fetcher.blocked))} refused this server's "
                + (
                    "address; the run went through the proxy instead"
                    if fetcher.proxy
                    else "address and no proxy is configured — set SCRAPE_PROXY"
                ),
            )
        # What DataImpulse will invoice. Zero with no proxy configured, and
        # always below `bytes` while pictures stay on the CDN exemption.
        stage.set("proxy_bytes", fetcher.proxy_wire)
        stage.set("stored", len(stored))
        # What this reader actually caused to be sent, as opposed to what it
        # merely wrote down. The two differ for a district still settling, and
        # that difference is the first thing to look at when a scraper is busy
        # and nobody is hearing from it.
        stage.set("announced", len(announce))
        stage.count("stored_not_announced", len(stored) - len(announce))

    return Sweep(stored, announce, fetcher.wire, fetcher.proxy_wire)


def _announceable(portal: Portal, catch: Catch, since: datetime | None) -> bool:
    """Whether anybody should be told about this listing."""

    if since is None:
        # Not watching this district yet — this run is what starts the watch,
        # and nothing visible before it began is news.
        return False
    if not portal.dated:
        # An undated portal has no per-listing evidence, so the district's own
        # settled state is all there is to go on, and it is settled.
        return True
    # A portal that publishes dates is held to them. A listing with no date
    # from a portal that normally gives one is not evidence of anything, so it
    # is stored and not announced.
    return catch.first_listed is not None and catch.first_listed > since


__all__ = [
    "DISTRICT_BUDGET", "GAP_HOURS", "PAUSE_SECONDS", "POSTCODE_BUDGET",
    "REFUSALS_ALLOWED", "Catch", "Harvest",
    "Kept", "Memory", "Portal", "Sweep", "collect", "keep", "stale_watches",
    "sweep_order",
]
