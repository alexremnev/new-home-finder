"""Which districts a run reads, and whose watch can still be trusted.

Two decisions that have nothing to do with any particular portal, and that are
easy to get quietly wrong: one produces a flood of alerts, the other produces
silence. Both are pure functions so that neither needs a database to pin down.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from worker import store
from worker.contracts.listing import Listing
from worker.sources import geo, sweep
from worker.sources.fetch import Fetcher, Refused
from worker.sources.sweep import GAP_HOURS, Catch, Harvest, stale_watches, sweep_order
from worker.store import Sighted, Watch

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def watch(settled_hours_ago: int, swept_hours_ago: int | None) -> Watch:
    return Watch(
        settled_at=NOW - timedelta(hours=settled_hours_ago),
        swept_at=None if swept_hours_ago is None else NOW - timedelta(hours=swept_hours_ago),
    )


# ── a watch with a hole in it ────────────────────────────────────────────

def test_a_district_read_minutes_ago_is_trusted() -> None:
    watching = {"E14": watch(settled_hours_ago=500, swept_hours_ago=0)}
    assert stale_watches(["E14"], watching, now=NOW) == []


def test_the_overnight_pause_is_not_a_hole() -> None:
    # The schedule deliberately does not run between 22:00 and 07:20 on a
    # weekday, and stops at 20:00 at the weekend — so the morning's first run
    # finds a gap of nine hours on a weekday and fourteen at the weekend. If
    # that counted, every district would restart its watch every morning and
    # nobody would ever be told anything.
    for quiet in (9, 14, GAP_HOURS - 1):
        watching = {"E14": watch(settled_hours_ago=500, swept_hours_ago=quiet)}
        assert stale_watches(["E14"], watching, now=NOW) == [], quiet


def test_a_district_unread_for_a_day_has_its_watch_voided() -> None:
    # This is the case that matters. A district leaves the wanted list the
    # moment it leaves the last subscription naming it, and if it comes back a
    # week later `settled_at` still claims a week of coverage that never
    # happened — so every listing published in the gap looks like news and
    # arrives at once.
    watching = {"E14": watch(settled_hours_ago=500, swept_hours_ago=GAP_HOURS + 1)}
    assert stale_watches(["E14"], watching, now=NOW) == ["E14"]


def test_a_week_long_gap_is_voided_too() -> None:
    watching = {"SE16": watch(settled_hours_ago=1000, swept_hours_ago=24 * 7)}
    assert stale_watches(["SE16"], watching, now=NOW) == ["SE16"]


def test_a_district_settled_this_run_is_not_stale_on_the_next() -> None:
    # The loop this closes: `settle_district` left `swept_at` NULL, and NULL
    # reads as a gap — so a district settled on one run was voided on the next,
    # settled again, voided again, once per run for ever. Nothing was ever
    # announced from it, and the warning fired thirty-eight times in a day
    # before anybody looked.
    #
    # Settling happens because we have just read the district, so "when did we
    # last read it" is now, and it is written at the same time.
    just_settled = Watch(settled_at=NOW, swept_at=NOW)
    assert stale_watches(["E14"], {"E14": just_settled}, now=NOW) == []


def test_a_watch_that_never_recorded_a_sweep_is_voided() -> None:
    # `swept_at` is NULL for rows written before 0050. Not knowing when a
    # district was last read is the same as not knowing whether there is a
    # gap, and guessing in the generous direction is how a flood happens.
    watching = {"E14": watch(settled_hours_ago=48, swept_hours_ago=None)}
    assert stale_watches(["E14"], watching, now=NOW) == ["E14"]


def test_a_district_we_never_watched_is_not_stale() -> None:
    # There is nothing to void: it has never claimed coverage, and the first
    # pass will start its watch in the ordinary way.
    assert stale_watches(["E14"], {}, now=NOW) == []


def test_only_wanted_districts_are_considered() -> None:
    # A district nobody subscribes to is not read and not restarted. Voiding
    # it would be churn for a district no run will touch.
    watching = {"E14": watch(settled_hours_ago=500, swept_hours_ago=999)}
    assert stale_watches([], watching, now=NOW) == []


# ── the order districts are read in ──────────────────────────────────────

def test_a_district_nobody_has_watched_yet_goes_first() -> None:
    # A waiting subscriber hears nothing at all until their district has been
    # read through once, so that comes before an even sweep of the rest.
    watching = {"E14": watch(500, 0), "SE16": watch(500, 1)}
    order = sweep_order(["E14", "SE16", "N1"], watching)
    assert order[0] == "N1"


def test_watched_districts_are_read_least_recently_first() -> None:
    # Not alphabetically. With more districts than the run budget, sorting by
    # name meant the same first twenty-five were read every run and everything
    # past them was never read at all — which, now that an unread district has
    # its watch voided, would leave them permanently restarting.
    watching = {
        "E14": watch(500, 1),    # read an hour ago
        "N1": watch(500, 9),     # nine hours ago
        "SE16": watch(500, 4),   # four hours ago
    }
    assert sweep_order(["E14", "N1", "SE16"], watching) == ["N1", "SE16", "E14"]


def test_every_district_is_offered_even_when_the_budget_will_cut_the_list() -> None:
    # The function returns all of them; the budget slices. What matters is
    # that the slice rotates, so run after run reaches a different tail.
    watching = {f"E{n}": watch(500, n) for n in range(1, 31)}
    order = sweep_order(list(watching), watching)
    assert len(order) == 30
    # The least recently read is first, the most recently read is last.
    assert order[0] == "E30" and order[-1] == "E1"


def test_the_order_is_stable_when_nothing_distinguishes_two_districts() -> None:
    # Same sweep time, so the tie breaks on the name. Without a tiebreak the
    # order would wobble between runs and the budget would cut a different
    # arbitrary set each time.
    watching = {"SE16": watch(500, 3), "E14": watch(500, 3)}
    assert sweep_order(["SE16", "E14"], watching) == ["E14", "SE16"]


class Counting:
    """Just enough of a Stage to see what the derivation reported."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def count(self, name: str, delta: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + delta


def catch(
    external_id: str, *, postcode: str | None = None,
    lat: float | None = 51.5, lng: float | None = -0.02,
    district: str | None = "E14",
) -> Catch:
    return Catch(listing=Listing(
        source_key="zoopla", external_id=external_id,
        url=f"https://example.test/{external_id}", price_pcm=2000, bedrooms=2,
        postcode=postcode, postcode_district=district, lat=lat, lng=lng,
    ))


def test_only_listings_that_need_a_postcode_and_can_have_one_are_asked_about(
    monkeypatch: Any,
) -> None:
    asked: list[list[geo.Ask]] = []
    monkeypatch.setattr(geo, "nearest", lambda asks: asked.append(asks) or {})

    stage = Counting()
    sweep._derive_postcodes(
        [
            catch("needs-it"),
            # Already has one from the portal: asking would cost a slot and
            # could only replace a fact with a guess.
            catch("has-one", postcode="E14 4AP"),
            # Nowhere to look.
            catch("no-coords", lat=None, lng=None),
        ],
        stage,  # type: ignore[arg-type]
    )

    assert [one.key for one in asked[0]] == ["needs-it"]
    assert stage.counts["postcode_asked"] == 1


def test_the_district_goes_with_the_question() -> None:
    # The refusal lives in `geo`, but it can only refuse if the district is
    # passed — so this is the half that has to be checked here.
    import unittest.mock as mock

    with mock.patch.object(geo, "nearest", return_value={}) as asking:
        sweep._derive_postcodes([catch("one", district="SE16")], Counting())  # type: ignore[arg-type]

    assert asking.call_args.args[0][0].district == "SE16"


def test_what_could_not_be_derived_is_counted_and_not_raised(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(geo, "nearest", lambda asks: {"b": "E14 4AP"})

    stage = Counting()
    found = sweep._derive_postcodes([catch("a"), catch("b")], stage)  # type: ignore[arg-type]

    assert found == {"b": "E14 4AP"}
    assert stage.counts["postcode_not_derived"] == 1


def test_nothing_to_ask_about_costs_no_request(monkeypatch: Any) -> None:
    def never(asks: list[geo.Ask]) -> dict[str, str]:
        raise AssertionError("asked with nothing to ask about")

    monkeypatch.setattr(geo, "nearest", never)

    assert sweep._derive_postcodes([catch("a", postcode="E14 4AP")], Counting()) == {}  # type: ignore[arg-type]


# ── what a run that read nothing is worth ────────────────────────────────
#
# The rule being pinned here is `collect`'s verdict, not the fetcher's
# retrying: a sweep that was refused everywhere it asked has to record
# `degraded`, and a sweep that read every district and found nothing new has
# to stay `ok`. Those two look identical in the counters — 0 stored either
# way — and telling them apart is the whole point.
#
# Driven through `collect` with the database stubbed out rather than through a
# helper, because the bug was never in a helper. `zoopla_london` sweeps one
# name, so one refusal is a whole run lost, and the consecutive-refusals rule
# that decides when to stop spending cannot reach three to say so. Both runs
# on 8 October 2026 that read nothing were recorded ok.

class Note:
    """A stage that remembers what it was told."""

    def __init__(self) -> None:
        self.status = "ok"
        self.counts: dict[str, int] = {}
        self.values: dict[str, Any] = {}
        self.said: list[str] = []

    def log(self, level: str, message: str, **_: object) -> None:
        self.said.append(f"{level}: {message}")

    def count(self, name: str, delta: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + delta

    def set(self, name: str, value: Any) -> None:
        self.values[name] = value

    def degrade(self, reason: str) -> None:
        self.status = "degraded"
        self.log("warn", reason)


class Job:
    """Just enough of `obs.log.Run` to be recorded against."""

    def __init__(self) -> None:
        self.stages: list[Note] = []

    @contextmanager
    def stage(self, name: str, *, source_key: str | None = None) -> Iterator[Note]:
        note = Note()
        self.stages.append(note)
        yield note


class Portal:
    """A portal that answers some districts and refuses the rest."""

    key = "zoopla"
    dated = True

    def __init__(self, *, refuses: set[str]) -> None:
        self.refuses = refuses
        self.asked: list[str] = []

    def harvest(self, district: str, *_: object) -> Harvest:
        self.asked.append(district)
        if district in self.refuses:
            raise Refused(
                f"https://example.test/{district} answered 403 under every "
                f"fingerprint tried, from the proxy"
            )
        return Harvest(caught=[])


def sweeping(monkeypatch: Any, districts: list[str]) -> None:
    """The database, answering as it would for a reader watching `districts`."""

    monkeypatch.setattr(store, "subscribed_districts", lambda conn: set(districts))
    monkeypatch.setattr(store, "district_watch", lambda conn, key: {})
    monkeypatch.setattr(
        store,
        "sighted_by",
        lambda conn, **_: Sighted(ids={}, known=frozenset()),
    )
    for quiet in ("record_sightings", "settle_district", "mark_swept"):
        monkeypatch.setattr(store, quiet, lambda *_, **__: None)


def swept(monkeypatch: Any, districts: list[str], refuses: set[str]) -> Note:
    sweeping(monkeypatch, districts)
    job = Job()
    sweep.collect(
        None,  # type: ignore[arg-type]
        job,  # type: ignore[arg-type]
        Portal(refuses=refuses),  # type: ignore[arg-type]
        pause=0,
        get=Fetcher(),
    )
    return job.stages[0]


def test_a_sweep_refused_everywhere_it_asked_is_degraded(monkeypatch: Any) -> None:
    stage = swept(monkeypatch, ["E14"], refuses={"E14"})

    # The case the dashboard was green through: one district, one refusal,
    # nothing read, and the consecutive count stuck at one of the three that
    # used to be what set the status.
    assert stage.status == "degraded"
    assert stage.counts["refused"] == 1
    assert stage.values["districts_read"] == 0


def test_every_district_refused_is_degraded_whatever_the_count(monkeypatch: Any) -> None:
    # Below REFUSALS_ALLOWED and still a run that read nothing.
    stage = swept(monkeypatch, ["E14", "SE16"], refuses={"E14", "SE16"})

    assert stage.status == "degraded"
    assert stage.values["districts_read"] == 0


def test_a_quiet_sweep_that_read_everything_stays_ok(monkeypatch: Any) -> None:
    # Nothing new on either district, which is what most runs are. Same 0
    # stored as the refused run above, and not a fault.
    stage = swept(monkeypatch, ["E14", "SE16"], refuses=set())

    assert stage.status == "ok"
    assert stage.counts.get("refused", 0) == 0
    assert stage.values["districts_read"] == 2


def test_one_refusal_among_districts_that_were_read_is_a_note_not_a_fault(
    monkeypatch: Any,
) -> None:
    stage = swept(monkeypatch, ["E14", "SE16"], refuses={"E14"})

    assert stage.status == "ok"
    assert stage.counts["refused"] == 1
    assert stage.values["districts_read"] == 1


def test_the_refusal_is_described_once_however_many_districts_hit_it(
    monkeypatch: Any,
) -> None:
    # It used to be once per unbroken streak, so a portal refusing the first
    # and the tenth district said the same sentence twice about one fault.
    stage = swept(
        monkeypatch, ["E14", "SE16", "SW11"], refuses={"E14", "SW11"}
    )

    assert stage.status == "ok"
    assert sum("answered 403" in one for one in stage.said) == 1


# ── a region reader's own watch ──────────────────────────────────────────
#
# `source_sweeps` holds district names upper-cased: both `settle_district` and
# `mark_swept` upper-case what they write, and `subscribed_districts` selects
# `upper(area)`, so the district path agrees with the table by construction.
#
# A region name did not. `zoopla.REGION` and `spareroom.REGION` are both the
# lowercase url slug "london", so the lookup missed the row that said LONDON
# and the engine told the reader on every run that it had never watched this
# name — which makes `_announceable` refuse everything and `harvest` read one
# page and stop. zoopla_london stored listings and announced none of them from
# the day it was deployed. Hence these two.


class Region:
    """A region reader, remembering what the engine told it to read back to."""

    key = "zoopla"
    dated = True
    regions = ("london",)

    def __init__(self) -> None:
        self.since: list[datetime | None] = []
        self.asked: list[str] = []

    def harvest(
        self,
        district: str,
        get: object,
        stage: object,
        since: datetime | None,
        memory: object = None,
    ) -> Harvest:
        self.asked.append(district)
        self.since.append(since)
        return Harvest(caught=[])


def region_swept(monkeypatch: Any, watching: dict[str, Watch]) -> Region:
    monkeypatch.setattr(store, "subscribed_districts", lambda conn: {"SE16"})
    monkeypatch.setattr(store, "district_watch", lambda conn, key: dict(watching))
    monkeypatch.setattr(
        store, "sighted_by", lambda conn, **_: Sighted(ids={}, known=frozenset())
    )
    for quiet in (
        "record_sightings",
        "settle_district",
        "mark_swept",
        "void_watch",
        "seen_ids",
        "remember_seen",
    ):
        monkeypatch.setattr(store, quiet, lambda *_, **__: None)
    portal = Region()
    sweep.collect(
        None,  # type: ignore[arg-type]
        Job(),  # type: ignore[arg-type]
        portal,  # type: ignore[arg-type]
        pause=0,
        get=Fetcher(),
    )
    return portal


def test_a_settled_region_is_read_back_to_the_last_sweep(monkeypatch: Any) -> None:
    # The watch exists and is recent, so the reader must be told when we last
    # swept. None here is the engine saying "never watched", which is the bug:
    # the whole of the announcing rule hangs off it.
    #
    # Timed against the real clock rather than this module's NOW, because
    # `stale_watches` reads the real one: a watch last swept in September is a
    # day-old gap today and would be voided before the lookup is reached, so
    # the fixed NOW would pass this test for the wrong reason.
    swept_at = datetime.now(UTC) - timedelta(minutes=5)
    portal = region_swept(
        monkeypatch,
        {
            "LONDON": Watch(
                settled_at=swept_at - timedelta(days=20), swept_at=swept_at
            )
        },
    )

    assert portal.since == [swept_at]


def test_the_region_is_swept_under_the_name_the_table_holds(
    monkeypatch: Any,
) -> None:
    # Upper-cased by `collect`, so the watch, the gap rule and the mark all
    # agree. Both readers lower-case it again for the url.
    portal = region_swept(monkeypatch, {})

    assert portal.asked == ["LONDON"]
