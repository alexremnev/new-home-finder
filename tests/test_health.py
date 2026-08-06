from __future__ import annotations

from worker.contracts.source import FieldSpec
from worker.extract.health import evaluate

FIELDS = (
    FieldSpec(name="external_id", required=True, kind="str", hint="listing id"),
    FieldSpec(name="price_pcm", required=True, kind="int", hint="monthly price"),
    FieldSpec(name="bedrooms", required=False, kind="int", hint="bedroom count"),
)


def rows(n: int, **overrides: object) -> list[dict[str, object]]:
    out = []
    for i in range(n):
        row = {"external_id": str(1000 + i), "price_pcm": 1950, "bedrooms": 2}
        row.update(overrides)
        out.append(row)
    return out


def test_healthy_extraction() -> None:
    health = evaluate(rows(50), FIELDS, min_items=5)
    assert health.verdict == "ok"
    assert health.passed
    assert health.fill_rate["price_pcm"] == 1.0


def test_zero_items_is_broken() -> None:
    """An empty result is a break, never a quiet market."""
    health = evaluate([], FIELDS, min_items=5)
    assert health.verdict == "broken"
    assert not health.passed


def test_below_floor_is_broken() -> None:
    assert evaluate(rows(2), FIELDS, min_items=5).verdict == "broken"


def test_duplicate_ids_signal_a_wrong_selector() -> None:
    """The same id twice on one page means the item selector matched too high."""
    duplicated = rows(10)
    for row in duplicated:
        row["external_id"] = "same"
    health = evaluate(duplicated, FIELDS, min_items=5)
    assert health.verdict == "broken"
    assert health.id_duplicates == 9


def test_missing_required_field_is_broken() -> None:
    health = evaluate(rows(20, price_pcm=None), FIELDS, min_items=5)
    assert health.verdict == "broken"
    assert health.fill_rate["price_pcm"] == 0.0


def test_optional_field_may_be_absent() -> None:
    """Only required fields gate. Optional ones are genuinely optional."""
    health = evaluate(rows(20, bedrooms=None), FIELDS, min_items=5)
    assert health.verdict == "ok"


def blanks(n: int) -> list[dict[str, object]]:
    return [{"external_id": f"b{i}", "price_pcm": None, "bedrooms": 1} for i in range(n)]


def test_fill_rate_boundary() -> None:
    """The floor is inclusive: exactly 95% filled is acceptable.

    Both sides matter. Too strict and a page with a couple of genuinely blank
    fields is declared broken, which spends tokens rewriting a correct schema;
    too lax and a real break goes unnoticed.
    """
    # 18/20 = 90%, below the floor.
    assert evaluate(rows(18) + blanks(2), FIELDS, min_items=5).verdict == "broken"
    # 19/20 = exactly 95%, at the floor and therefore acceptable.
    assert evaluate(rows(19) + blanks(1), FIELDS, min_items=5).verdict == "ok"
    # 39/40 = 97.5%, comfortably above.
    assert evaluate(rows(39) + blanks(1), FIELDS, min_items=5).verdict == "ok"


def test_weekly_price_read_as_monthly_is_caught() -> None:
    """The failure a schema cannot see: a field whose meaning changed.

    Extraction succeeds and every field is filled, but the values are a quarter
    of what they should be. Only a range check notices.
    """
    health = evaluate(rows(20, price_pcm=450 // 4), FIELDS, min_items=5)
    assert health.verdict == "broken"
    assert health.implausible == 20


def test_implausible_bedroom_count_is_caught() -> None:
    health = evaluate(rows(20, bedrooms=87), FIELDS, min_items=5)
    assert health.verdict == "broken"


def test_a_single_odd_row_does_not_break_a_good_page() -> None:
    mixed = rows(19) + [{"external_id": "z", "price_pcm": 1, "bedrooms": 2}]
    assert evaluate(mixed, FIELDS, min_items=5).verdict == "ok"


def test_volume_anomaly_degrades_without_touching_the_schema() -> None:
    """A partial break warns; it must not trigger a schema rewrite."""
    health = evaluate(rows(10), FIELDS, min_items=5, recent_average=100)
    assert health.verdict == "degraded"
    assert health.passed  # degraded still counts as usable


def test_normal_volume_against_the_average_is_fine() -> None:
    assert evaluate(rows(90), FIELDS, min_items=5, recent_average=100).verdict == "ok"


def test_notes_explain_the_verdict() -> None:
    health = evaluate([], FIELDS, min_items=5)
    assert health.notes and "empty market" in health.notes[0]
