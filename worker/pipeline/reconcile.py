"""Reconciliation decisions.

Every rule about a listing's life is decided here, in functions that touch no
database. Persistence is a thin layer around them, so the subtle parts — what
counts as new, when a listing is really gone, when to leave the database alone —
are testable without Postgres.

Four rules carry the weight, and each exists because of a specific way this goes
wrong:

  * a listing is marked delisted only after two consecutive misses, so one network
    failure does not empty the database;
  * misses are only counted during a full pass. A hot run reads the first pages of
    a newest-first index and by construction cannot see older listings, so
    counting misses there would delist everything older than a day;
  * while a source is unhealthy nothing is delisted at all. A broken parser
    returning nothing looks exactly like every listing disappearing at once;
  * a price change and a return from delisted are both updates, not new listings.
    Treating either as new would re-notify people about something they have
    already seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DELIST_AFTER_MISSES = 2

Kind = Literal["insert", "update", "unchanged", "revive", "miss", "delist", "hold"]

# Fields worth an UPDATE when they change. Deliberately excludes timestamps and
# anything derived, so an unchanged listing produces no write.
TRACKED = (
    "price_pcm",
    "bedrooms",
    "bathrooms",
    "property_type",
    "furnished",
    "pets_allowed",
    "bills_included",
    "available_from",
    "min_tenancy_months",
    "deposit_pcm",
    "postcode",
    "postcode_district",
    "tfl_zone",
    "title",
    "is_landlord_direct",
)


@dataclass(frozen=True)
class Existing:
    """The stored state of a listing, as far as reconciliation cares."""

    id: int
    status: str
    miss_count: int
    values: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    kind: Kind
    notify: bool = False
    """Whether this listing should reach the matcher. Only a first sighting does."""
    price_change: tuple[object, object] | None = None
    changed_fields: tuple[str, ...] = ()
    miss_count: int = 0
    reason: str = ""


def decide_seen(existing: Existing | None, incoming: dict[str, object]) -> Decision:
    """Decide what to do with a listing that the source returned."""
    if existing is None:
        return Decision(kind="insert", notify=True, reason="first sighting")

    changed = tuple(
        name
        for name in TRACKED
        if name in incoming and incoming[name] != existing.values.get(name)
    )
    old_price = existing.values.get("price_pcm")
    new_price = incoming.get("price_pcm")
    price_change = (
        (old_price, new_price)
        if "price_pcm" in incoming and old_price != new_price
        else None
    )

    if existing.status == "delisted":
        # Back on the market. Not new: the recipient has already seen it, and the
        # send-history key would block a second message anyway.
        return Decision(
            kind="revive",
            notify=False,
            price_change=price_change,
            changed_fields=changed,
            reason="relisted after being marked delisted",
        )

    if changed:
        return Decision(
            kind="update",
            notify=False,
            price_change=price_change,
            changed_fields=changed,
            reason=f"changed: {', '.join(changed)}",
        )

    return Decision(kind="unchanged", reason="no tracked field changed")


def decide_missing(
    existing: Existing, *, mode: str, source_health: str
) -> Decision:
    """Decide what to do with a stored listing the source did not return."""
    if source_health != "ok":
        return Decision(
            kind="hold",
            miss_count=existing.miss_count,
            reason=f"source health is {source_health}; delisting suspended",
        )

    if mode != "sweep":
        return Decision(
            kind="hold",
            miss_count=existing.miss_count,
            reason="a hot run does not see older listings, so absence proves nothing",
        )

    if existing.status == "delisted":
        return Decision(kind="hold", miss_count=existing.miss_count, reason="already delisted")

    misses = existing.miss_count + 1
    if misses >= DELIST_AFTER_MISSES:
        return Decision(
            kind="delist",
            miss_count=misses,
            reason=f"absent from {misses} consecutive full passes",
        )
    return Decision(
        kind="miss",
        miss_count=misses,
        reason=f"absent once; delisted after {DELIST_AFTER_MISSES}",
    )


@dataclass
class Summary:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    revived: int = 0
    missed: int = 0
    delisted: int = 0
    held: int = 0
    price_changes: int = 0
    new_ids: list[int] = field(default_factory=list)

    def record(self, decision: Decision, listing_id: int | None = None) -> None:
        counter = {
            "insert": "inserted",
            "update": "updated",
            "unchanged": "unchanged",
            "revive": "revived",
            "miss": "missed",
            "delist": "delisted",
            "hold": "held",
        }[decision.kind]
        setattr(self, counter, getattr(self, counter) + 1)
        if decision.price_change is not None:
            self.price_changes += 1
        if decision.notify and listing_id is not None:
            self.new_ids.append(listing_id)

    def as_counters(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "revived": self.revived,
            "missed": self.missed,
            "delisted": self.delisted,
            "held": self.held,
            "price_changes": self.price_changes,
            "new": len(self.new_ids),
        }
