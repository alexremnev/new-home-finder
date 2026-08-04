"""Extraction health gates.

These decide whether an extraction schema still works. They are the trigger for
automatic schema repair, which makes a false alarm expensive in both directions:
a missed break leaves the source silently empty, and a spurious break spends
tokens rewriting a schema that was correct.

The gates are therefore calibrated against a saved page rather than against
assumptions. An earlier version of the reachability probe demonstrated the
failure mode: it looked for the words "per month" on a site that writes "p/m",
and inside the first few kilobytes of a page a few hundred kilobytes long, so it
reported fully served pages as empty.

Plausibility checks exist for a specific case the schema cannot catch: a field
whose meaning changed. A weekly price read as monthly extracts cleanly and
normalises to a number four times too small, and only a range check notices.
"""

from __future__ import annotations

from collections.abc import Sequence

from worker.contracts.extraction import Health
from worker.contracts.listing import RawValue
from worker.contracts.source import FieldSpec

# Anomaly thresholds. `found` below this share of the recent average means a
# partial break: alert, but do not touch the schema.
VOLUME_ANOMALY_RATIO = 0.30
REQUIRED_FILL_FLOOR = 0.95
IMPLAUSIBLE_CEILING = 0.10

PRICE_PCM_RANGE = (200, 20_000)
BEDROOMS_RANGE = (0, 10)


def evaluate(
    rows: Sequence[dict[str, RawValue]],
    fields: Sequence[FieldSpec],
    *,
    min_items: int,
    id_field: str = "external_id",
    recent_average: float | None = None,
) -> Health:
    """Judge one extraction result."""
    notes: list[str] = []
    items = len(rows)

    required = [f.name for f in fields if f.required]
    fill = {
        name: (sum(1 for r in rows if _present(r.get(name))) / items if items else 0.0)
        for name in required
    }

    ids = [str(r.get(id_field)) for r in rows if _present(r.get(id_field))]
    duplicates = len(ids) - len(set(ids))

    implausible = sum(1 for r in rows if _implausible(r))

    verdict = "ok"
    if items == 0:
        verdict, note = "broken", "no items extracted; an empty market is not a real outcome"
        notes.append(note)
    elif items < min_items:
        verdict = "broken"
        notes.append(f"only {items} items, below the floor of {min_items}")
    elif duplicates:
        verdict = "broken"
        notes.append(
            f"{duplicates} duplicate ids on one page; the item selector is probably "
            "matching the wrong level of the document"
        )
    elif low := [n for n, v in fill.items() if v < REQUIRED_FILL_FLOOR]:
        verdict = "broken"
        notes.append(f"required fields under {REQUIRED_FILL_FLOOR:.0%} filled: {sorted(low)}")
    elif items and implausible / items > IMPLAUSIBLE_CEILING:
        verdict = "broken"
        notes.append(
            f"{implausible}/{items} rows implausible; a field's meaning may have changed"
        )
    elif recent_average and items < recent_average * VOLUME_ANOMALY_RATIO:
        verdict = "degraded"
        notes.append(
            f"{items} items against a recent average of {recent_average:.0f}; "
            "possible partial break, schema left alone"
        )

    return Health(
        items_found=items,
        fill_rate=fill,
        implausible=implausible,
        id_duplicates=duplicates,
        verdict=verdict,
        notes=notes,
    )


def _present(value: RawValue) -> bool:
    return value is not None and str(value).strip() != ""


def _implausible(row: dict[str, RawValue]) -> bool:
    price = _number(row.get("price_pcm"))
    if price is not None and not PRICE_PCM_RANGE[0] <= price <= PRICE_PCM_RANGE[1]:
        return True
    beds = _number(row.get("bedrooms"))
    return beds is not None and not BEDROOMS_RANGE[0] <= beds <= BEDROOMS_RANGE[1]


def _number(value: RawValue) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
