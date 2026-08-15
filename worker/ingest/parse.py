"""Turning stored messages into listings.

The second half of ingest, and deliberately a separate job from the first. The
reader is tied to one host and one Telegram session; this is a pure transformation
over rows that can run anywhere, as often as you like, and be re-run over the same
rows after the parser improves. Splitting them is what makes a format change cost
an afternoon instead of a week of lost listings.

Nothing here decides what a message means — `tg_feed.parse` does, without a
database — and nothing here writes a listing by hand: a parsed message becomes a
`Listing`, and `store.insert_listing` puts it away exactly as a scraped one. One
write path, one set of constraints, one place where `UNIQUE (source_key,
external_id)` deduplicates a feed's listing against the same listing found later by
the scraper.
"""

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

# How many messages one pass converts. Bounded for the same reason the reader is:
# a first run over months of history must not hold all of it in memory, and what is
# left over is simply the next run's work.
BATCH = 500


def as_listing(parsed: tg_feed.Parsed) -> Listing:
    """A parsed message in the shape every other source already writes.

    `Listing` validates on construction — a price outside its bounds, a bedroom
    count above twenty — so a source that starts emitting nonsense is refused here
    rather than stored and matched against.
    """
    return Listing(
        source_key=parsed.source_key,
        external_id=parsed.external_id,
        url=parsed.url,
        price_pcm=parsed.price_pcm,
        bedrooms=parsed.bedrooms,
        bathrooms=parsed.bathrooms,
        property_type=parsed.property_type,
        furnished=parsed.furnished,  # type: ignore[arg-type]
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
    """Convert what is waiting. Returns the listing ids written, for the matcher.

    The ids are returned rather than matched here because matching is somebody
    else's stage: `queue_matches` already knows how to take a list of new listing
    ids, and calling it from inside a parser would put two decisions in one job.
    """
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
                    # The reader stored every url it found, buttons included, so the
                    # parser gets them as bare strings rather than button objects.
                    list(message["links"] or []),
                    received_at=message["received_at"],
                    message_id=int(message["external_id"]) if str(message["external_id"]).isdigit()
                    else None,
                )
            except tg_feed.Unparseable as reason:
                # Expected, and not an error: a welcome message, an advert, a format
                # that moved. Recorded with its reason and never retried, so the
                # count of each reason is a readable signal.
                store.mark_unparseable(conn, message_id, str(reason))
                stage.count(f"unparseable:{str(reason).split(';')[0][:40]}")
                continue

            try:
                listing = as_listing(parsed)
            except ValidationError as error:
                store.mark_unparseable(conn, message_id, f"invalid listing: {error}")
                stage.count("invalid")
                continue

            # Before the listing, so that a district arriving for the first time is
            # nameable in the wizard from the moment its first listing exists. The
            # feed reaches outer London, which the zone 1-3 reference data does not,
            # and a listing nobody can filter for is a listing nobody receives.
            if listing.postcode_district:
                if store.ensure_district(
                    conn, listing.postcode_district, source_key=source_key
                ):
                    stage.count("district_discovered")

            listing_id = store.insert_listing(conn, listing)
            store.mark_parsed(conn, message_id, listing_id)
            # Deduplicated by the insert, not by us: two messages about one listing
            # resolve to the same row, and the matcher must not be handed it twice.
            if listing_id not in written:
                written.append(listing_id)
            stage.count("parsed")

        stage.set("listings", len(written))
        return written


__all__ = ["BATCH", "as_listing", "run_parse"]
