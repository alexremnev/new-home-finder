"""Which districts a run reads, and whose watch can still be trusted.

Two decisions that have nothing to do with any particular portal, and that are
easy to get quietly wrong: one produces a flood of alerts, the other produces
silence. Both are pure functions so that neither needs a database to pin down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from worker.sources.sweep import GAP_HOURS, stale_watches, sweep_order
from worker.store import Watch

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
