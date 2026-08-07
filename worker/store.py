"""Persistence.

Deliberately thin. Every rule about a listing's life lives in
`worker.pipeline.reconcile` as a pure function; this module only reads and writes
what those functions decide. That split is what makes the subtle behaviour
testable without a database, and keeps the SQL simple enough to read.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from worker.contracts.extraction import ExtractionSchema, Health
from worker.contracts.listing import Listing
from worker.pipeline.reconcile import TRACKED, Existing

Row = dict[str, Any]
Conn = psycopg.Connection[Row]


# ── sources ───────────────────────────────────────────────────────────────


def source_row(conn: Conn, source_key: str) -> Row | None:
    return conn.execute("SELECT * FROM sources WHERE key = %s", (source_key,)).fetchone()


def set_source_health(
    conn: Conn,
    source_key: str,
    health: str,
    *,
    note: str | None = None,
    cooldown_seconds: float | None = None,
) -> None:
    conn.execute(
        """
        UPDATE sources
           SET health = %(health)s,
               health_note = %(note)s,
               health_until = CASE
                   WHEN %(cooldown)s IS NULL THEN NULL
                   ELSE now() + make_interval(secs => %(cooldown)s)
               END
         WHERE key = %(key)s
        """,
        {"key": source_key, "health": health, "note": note, "cooldown": cooldown_seconds},
    )


def clear_source_cooldown_if_elapsed(conn: Conn, source_key: str) -> Row | None:
    """Return a source to service once its cooldown has passed.

    Without this a circuit breaker would latch permanently, and the source would
    stay skipped long after the reason had gone.
    """
    conn.execute(
        """
        UPDATE sources
           SET health = 'ok', health_note = NULL, health_until = NULL
         WHERE key = %s AND health_until IS NOT NULL AND health_until <= now()
        """,
        (source_key,),
    )
    return source_row(conn, source_key)


# ── extraction schemas ────────────────────────────────────────────────────


def load_schema(conn: Conn, source_key: str, page_kind: str) -> tuple[int, ExtractionSchema] | None:
    row = conn.execute(
        """
        SELECT id, schema FROM parse_schemas
         WHERE source_key = %s AND page_kind = %s AND status IN ('active', 'pinned')
        """,
        (source_key, page_kind),
    ).fetchone()
    if row is None:
        return None
    return row["id"], ExtractionSchema.model_validate(row["schema"])


def save_schema(
    conn: Conn,
    *,
    source_key: str,
    page_kind: str,
    schema: ExtractionSchema,
    layout_fingerprint: str,
    status: str,
    created_by: str,
    validation: Health | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> int:
    """Store a schema and make it the one in force.

    The previous one is retired rather than deleted: it is keyed by the layout it
    was written for, so if the site reverts an experiment it can be reactivated
    without asking a model anything.
    """
    conn.execute(
        """
        UPDATE parse_schemas SET status = 'retired'
         WHERE source_key = %s AND page_kind = %s AND status IN ('active', 'pinned')
        """,
        (source_key, page_kind),
    )
    row = conn.execute(
        """
        INSERT INTO parse_schemas
               (source_key, page_kind, version, layout_fingerprint, strategy, schema,
                status, created_by, validation, tokens_in, tokens_out, activated_at)
        SELECT %(source)s, %(kind)s,
               coalesce(max(version), 0) + 1,
               %(fingerprint)s, %(strategy)s, %(schema)s, %(status)s, %(by)s,
               %(validation)s, %(tin)s, %(tout)s, now()
          FROM parse_schemas WHERE source_key = %(source)s AND page_kind = %(kind)s
        RETURNING id
        """,
        {
            "source": source_key,
            "kind": page_kind,
            "fingerprint": layout_fingerprint,
            "strategy": schema.strategy,
            "schema": Jsonb(schema.model_dump(exclude_none=True)),
            "status": status,
            "by": created_by,
            "validation": Jsonb(validation.model_dump()) if validation else None,
            "tin": tokens_in,
            "tout": tokens_out,
        },
    ).fetchone()
    assert row is not None
    return int(row["id"])


def schema_for_fingerprint(
    conn: Conn, source_key: str, page_kind: str, layout_fingerprint: str
) -> tuple[int, ExtractionSchema] | None:
    """A retired schema written for this exact layout, if there is one."""
    row = conn.execute(
        """
        SELECT id, schema FROM parse_schemas
         WHERE source_key = %s AND page_kind = %s AND layout_fingerprint = %s
           AND status = 'retired'
         ORDER BY version DESC LIMIT 1
        """,
        (source_key, page_kind, layout_fingerprint),
    ).fetchone()
    if row is None:
        return None
    return row["id"], ExtractionSchema.model_validate(row["schema"])


# ── listings ──────────────────────────────────────────────────────────────


def known_external_ids(conn: Conn, source_key: str) -> set[str]:
    """Every id already stored, so a listing costs no request to re-discover."""
    rows = conn.execute(
        "SELECT external_id FROM listings WHERE source_key = %s", (source_key,)
    ).fetchall()
    return {r["external_id"] for r in rows}


def existing_listing(conn: Conn, source_key: str, external_id: str) -> Existing | None:
    columns = ", ".join(TRACKED)
    row = conn.execute(
        f"SELECT id, status, miss_count, {columns} FROM listings "  # noqa: S608 - fixed names
        "WHERE source_key = %s AND external_id = %s",
        (source_key, external_id),
    ).fetchone()
    if row is None:
        return None
    return Existing(
        id=row["id"],
        status=row["status"],
        miss_count=row["miss_count"],
        values={name: row[name] for name in TRACKED},
    )


def active_listing_ids(conn: Conn, source_key: str) -> dict[str, Existing]:
    """Everything currently active, keyed by external id, for a full pass."""
    columns = ", ".join(TRACKED)
    rows = conn.execute(
        f"SELECT external_id, id, status, miss_count, {columns} FROM listings "  # noqa: S608
        "WHERE source_key = %s AND status = 'active'",
        (source_key,),
    ).fetchall()
    return {
        r["external_id"]: Existing(
            id=r["id"], status=r["status"], miss_count=r["miss_count"],
            values={name: r[name] for name in TRACKED},
        )
        for r in rows
    }


_INSERT_COLUMNS = (
    "source_key", "external_id", "url", "price_pcm", "bedrooms", "bathrooms",
    "property_type", "furnished", "pets_allowed", "bills_included", "available_from",
    "min_tenancy_months", "deposit_pcm", "postcode", "postcode_district", "tfl_zone",
    "lat", "lng", "title", "description", "is_landlord_direct", "photo_count",
)


def insert_listing(conn: Conn, listing: Listing, *, schema_id: int | None = None) -> int:
    values = _listing_values(listing)
    columns = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(f"%({name})s" for name in _INSERT_COLUMNS)
    row = conn.execute(
        f"""
        INSERT INTO listings ({columns}, schema_id, raw)
        VALUES ({placeholders}, %(schema_id)s, %(raw)s)
        ON CONFLICT (source_key, external_id) DO UPDATE
           SET last_seen_at = now(), miss_count = 0
        RETURNING id
        """,  # noqa: S608 - column names are a fixed tuple in this module
        {**values, "schema_id": schema_id, "raw": Jsonb(listing.raw)},
    ).fetchone()
    assert row is not None
    return int(row["id"])


def update_listing(conn: Conn, listing_id: int, listing: Listing, *, revive: bool) -> None:
    values = _listing_values(listing)
    assignments = ", ".join(
        f"{name} = %({name})s" for name in _INSERT_COLUMNS if name not in ("source_key",
                                                                          "external_id")
    )
    conn.execute(
        f"""
        UPDATE listings
           SET {assignments},
               raw = %(raw)s,
               last_seen_at = now(),
               miss_count = 0,
               status = 'active',
               delisted_at = CASE WHEN %(revive)s THEN NULL ELSE delisted_at END
         WHERE id = %(listing_id)s
        """,  # noqa: S608 - assignments are built from a fixed tuple
        {**values, "raw": Jsonb(listing.raw), "revive": revive, "listing_id": listing_id},
    )


def touch_listing(conn: Conn, listing_id: int) -> None:
    """An unchanged listing: record that it is still there and nothing else."""
    conn.execute(
        "UPDATE listings SET last_seen_at = now(), miss_count = 0 WHERE id = %s",
        (listing_id,),
    )


def record_price(conn: Conn, listing_id: int, price_pcm: int) -> None:
    conn.execute(
        """
        INSERT INTO listing_price_log (listing_id, price_pcm) VALUES (%s, %s)
        ON CONFLICT (listing_id, seen_at) DO NOTHING
        """,
        (listing_id, price_pcm),
    )


def set_miss_count(conn: Conn, listing_id: int, miss_count: int) -> None:
    conn.execute("UPDATE listings SET miss_count = %s WHERE id = %s", (miss_count, listing_id))


def delist(conn: Conn, listing_id: int, miss_count: int) -> None:
    """Soft delete only. History is needed for analytics and for future dedup."""
    conn.execute(
        """
        UPDATE listings
           SET status = 'delisted', delisted_at = now(), miss_count = %s
         WHERE id = %s
        """,
        (miss_count, listing_id),
    )


def _listing_values(listing: Listing) -> dict[str, Any]:
    data = asdict(listing) if hasattr(listing, "__dataclass_fields__") else listing.model_dump()
    return {name: data.get(name) for name in _INSERT_COLUMNS}


# ── conditional request validators ────────────────────────────────────────


def load_validators(conn: Conn, source_key: str) -> dict[str, tuple[str | None, str | None]]:
    """ETag and Last-Modified per URL, so an unchanged page costs almost nothing."""
    row = conn.execute(
        "SELECT config -> 'validators' AS v FROM sources WHERE key = %s", (source_key,)
    ).fetchone()
    stored = (row or {}).get("v") or {}
    return {url: (pair.get("etag"), pair.get("modified")) for url, pair in stored.items()}


def save_validators(
    conn: Conn, source_key: str, validators: dict[str, tuple[str | None, str | None]], *,
    keep: int = 500,
) -> None:
    trimmed = dict(list(validators.items())[-keep:])
    payload = {
        url: {"etag": etag, "modified": modified} for url, (etag, modified) in trimmed.items()
    }
    conn.execute(
        "UPDATE sources SET config = jsonb_set(config, '{validators}', %s::jsonb) WHERE key = %s",
        (json.dumps(payload), source_key),
    )
