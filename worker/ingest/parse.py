from __future__ import annotations

from typing import Any

import psycopg
from pydantic import ValidationError

from worker import store
from worker.contracts.listing import Listing
from worker.ingest import tg_feed
from worker.obs import Run

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

BATCH = 500

def as_listing(parsed: tg_feed.Parsed) -> Listing:

    return Listing(
        source_key=parsed.source_key,
        external_id=parsed.external_id,
        url=parsed.url,
        price_pcm=parsed.price_pcm,
        bedrooms=parsed.bedrooms,
        bathrooms=parsed.bathrooms,
        property_type=parsed.property_type,
        furnished=parsed.furnished,
        available_from=parsed.available_from,
        deposit_pcm=parsed.deposit_pcm,
        postcode=parsed.postcode,
        postcode_district=parsed.postcode_district,
        raw=parsed.raw,
    )

def run_parse(
    conn: Conn, run: Run, *, source_key: str = "tg_feed", limit: int = BATCH,
    dry_run: bool = False,
) -> list[int]:

    with run.stage("parse", source_key=source_key) as stage:
        pending = store.unparsed_messages(conn, source_key=source_key, limit=limit)
        stage.set("pending", len(pending))
        if not pending:
            return []
        if dry_run:
            stage.set("suppressed", True)
            return []

        written: list[int] = []
        for message in pending:
            message_id = int(message["id"])
            try:
                parsed = tg_feed.parse(
                    message["body"] or "",

                    list(message["links"] or []),
                    received_at=message["received_at"],
                    message_id=int(message["external_id"]) if str(message["external_id"]).isdigit()
                    else None,
                )
            except tg_feed.Unparseable as reason:

                store.mark_unparseable(conn, message_id, str(reason))
                stage.count(f"unparseable:{str(reason).split(';')[0][:40]}")
                continue

            try:
                listing = as_listing(parsed)
            except ValidationError as error:
                store.mark_unparseable(conn, message_id, f"invalid listing: {error}")
                stage.count("invalid")
                continue

            if listing.postcode_district:
                if store.ensure_district(
                    conn, listing.postcode_district, source_key=source_key
                ):
                    stage.count("district_discovered")

            listing_id = store.insert_listing(conn, listing)
            store.mark_parsed(conn, message_id, listing_id)

            if listing_id not in written:
                written.append(listing_id)
            stage.count("parsed")

        stage.set("listings", len(written))
        return written

__all__ = ["BATCH", "as_listing", "run_parse"]
