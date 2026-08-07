from __future__ import annotations

from datetime import date

import pytest

from worker.pipeline.reconcile import (
    DELIST_AFTER_MISSES,
    Decision,
    Existing,
    Summary,
    decide_missing,
    decide_seen,
)


def stored(**overrides: object) -> Existing:
    values: dict[str, object] = {
        "price_pcm": 1950,
        "bedrooms": 2,
        "furnished": "furnished",
        "pets_allowed": None,
        "available_from": date(2026, 9, 1),
        "title": "2 Bed Flat, Rotherhithe Street, SE16",
    }
    status = str(overrides.pop("status", "active"))
    miss_count = int(overrides.pop("miss_count", 0))  # type: ignore[arg-type]
    values.update(overrides)
    return Existing(id=1, status=status, miss_count=miss_count, values=values)


def incoming(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "price_pcm": 1950,
        "bedrooms": 2,
        "furnished": "furnished",
        "pets_allowed": None,
        "available_from": date(2026, 9, 1),
        "title": "2 Bed Flat, Rotherhithe Street, SE16",
    }
    values.update(overrides)
    return values


# ── a listing the source returned ─────────────────────────────────────────


def test_a_listing_we_have_never_seen_is_new() -> None:
    decision = decide_seen(None, incoming())
    assert decision.kind == "insert"
    assert decision.notify is True


def test_an_unchanged_listing_produces_no_write() -> None:
    decision = decide_seen(stored(), incoming())
    assert decision.kind == "unchanged"
    assert decision.notify is False
    assert decision.changed_fields == ()


def test_a_changed_field_is_an_update_not_a_new_listing() -> None:
    decision = decide_seen(stored(), incoming(furnished="unfurnished"))
    assert decision.kind == "update"
    assert decision.notify is False
    assert decision.changed_fields == ("furnished",)


def test_a_price_change_is_recorded_but_is_not_new() -> None:
    """Re-notifying on a price change would tell people about a listing they have
    already been sent. The change is logged; the alert is a separate, opt-in
    feature."""
    decision = decide_seen(stored(), incoming(price_pcm=1800))
    assert decision.kind == "update"
    assert decision.notify is False
    assert decision.price_change == (1950, 1800)


def test_a_relisted_listing_is_not_new_either() -> None:
    """It disappeared and came back. The recipient has already seen it."""
    decision = decide_seen(stored(status="delisted", miss_count=2), incoming())
    assert decision.kind == "revive"
    assert decision.notify is False


def test_a_relisted_listing_can_also_have_changed_price() -> None:
    decision = decide_seen(stored(status="delisted"), incoming(price_pcm=1700))
    assert decision.kind == "revive"
    assert decision.price_change == (1950, 1700)


def test_none_and_false_are_different_values() -> None:
    """"Not stated" becoming "no" is a real change and must be written."""
    decision = decide_seen(stored(pets_allowed=None), incoming(pets_allowed=False))
    assert decision.kind == "update"
    assert decision.changed_fields == ("pets_allowed",)


def test_a_field_absent_from_the_extraction_is_not_treated_as_a_change() -> None:
    """A page that did not carry a field must not blank the stored value."""
    partial = {"price_pcm": 1950, "bedrooms": 2}
    decision = decide_seen(stored(), partial)
    assert decision.kind == "unchanged"


# ── a listing the source did not return ───────────────────────────────────


def test_one_miss_in_a_full_pass_does_not_delist() -> None:
    decision = decide_missing(stored(), mode="sweep", source_health="ok")
    assert decision.kind == "miss"
    assert decision.miss_count == 1


def test_a_second_consecutive_miss_delists() -> None:
    decision = decide_missing(stored(miss_count=1), mode="sweep", source_health="ok")
    assert decision.kind == "delist"
    assert decision.miss_count == DELIST_AFTER_MISSES


def test_a_hot_run_never_delists() -> None:
    """A hot run reads the first pages of a newest-first index, so it cannot see
    older listings. Counting misses there would delist everything older than a day."""
    decision = decide_missing(stored(miss_count=1), mode="hot", source_health="ok")
    assert decision.kind == "hold"
    assert "hot run" in decision.reason


@pytest.mark.parametrize("health", ["broken", "blocked", "degraded"])
def test_an_unhealthy_source_delists_nothing(health: str) -> None:
    """A parser returning nothing looks exactly like every listing vanishing.

    This is the guard that keeps a markup change from emptying the database.
    """
    decision = decide_missing(stored(miss_count=1), mode="sweep", source_health=health)
    assert decision.kind == "hold"
    assert health in decision.reason


def test_miss_count_is_not_advanced_while_holding() -> None:
    """A held decision must not creep towards delisting on runs that prove nothing."""
    existing = stored(miss_count=1)
    for mode, health in (("hot", "ok"), ("sweep", "broken")):
        decision = decide_missing(existing, mode=mode, source_health=health)
        assert decision.miss_count == 1


def test_an_already_delisted_listing_is_left_alone() -> None:
    decision = decide_missing(
        stored(status="delisted", miss_count=2), mode="sweep", source_health="ok"
    )
    assert decision.kind == "hold"


def test_seeing_a_listing_again_clears_the_miss_count() -> None:
    """Two non-consecutive misses must not add up to a delisting."""
    first = decide_missing(stored(miss_count=0), mode="sweep", source_health="ok")
    assert first.miss_count == 1
    # It reappears, so the counter resets; the next miss starts from one again.
    seen = decide_seen(stored(miss_count=1), incoming())
    assert seen.kind == "unchanged"
    later = decide_missing(stored(miss_count=0), mode="sweep", source_health="ok")
    assert later.kind == "miss"


# ── summary ───────────────────────────────────────────────────────────────


def test_summary_counts_and_collects_new_ids() -> None:
    summary = Summary()
    summary.record(Decision(kind="insert", notify=True), listing_id=10)
    summary.record(Decision(kind="insert", notify=True), listing_id=11)
    summary.record(Decision(kind="update", price_change=(1950, 1800)))
    summary.record(Decision(kind="unchanged"))
    summary.record(Decision(kind="delist", miss_count=2))
    summary.record(Decision(kind="hold"))

    counters = summary.as_counters()
    assert counters["inserted"] == 2
    assert counters["updated"] == 1
    assert counters["price_changes"] == 1
    assert counters["delisted"] == 1
    assert counters["held"] == 1
    assert counters["new"] == 2
    assert summary.new_ids == [10, 11]


def test_only_first_sightings_reach_the_matcher() -> None:
    summary = Summary()
    for decision in (
        Decision(kind="update", notify=False),
        Decision(kind="revive", notify=False),
        Decision(kind="unchanged"),
    ):
        summary.record(decision, listing_id=99)
    assert summary.new_ids == []
