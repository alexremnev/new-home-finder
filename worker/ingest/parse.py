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

# How a sighting from the Telegram feed is labelled in `listing_sightings`.
# Not a source key: the feed carries every portal, and which portal a listing
# is from is already `listings.source_key`.
FEED_READER = "tg_feed"

def as_listing(parsed: tg_feed.Parsed) -> Listing:

    raw = dict(parsed.raw)
    # The alert's "where" line reads `raw.address`, and a feed message that
    # states no Address or Location field leaves it empty — which on OpenRent
    # is most of them. Its url carries the building in the slug, so there is
    # something to say without fetching anything. Only as a fallback: what the
    # message stated is what the portal stated, and this is a reconstruction.
    #
    # OpenRent only, because only its urls carry a slug. Imported from the
    # reader that owns that shape rather than copied, the same way
    # `openrent_v2` borrows `read_slug`.
    if parsed.source_key == "openrent" and not raw.get("address"):
        from worker.sources.openrent import place_in_slug

        slug = parsed.url.rstrip("/").rsplit("/", 2)[-2] if "/" in parsed.url else ""
        raw["address"] = place_in_slug(slug) or ""

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
        floor_area_sqft=parsed.floor_area_sqft,
        postcode=parsed.postcode,
        postcode_district=parsed.postcode_district,
        raw=raw,
    )

# Bounded twice over. The job must not stall behind a slow portal, and at a
# two-minute interval a generous batch becomes a thousand requests an hour at a
# site that is doing us a favour by answering at all. Twelve is about 360 an
# hour while there is a backlog, and roughly the arrival rate once there is not.
IMAGE_BATCH = 12

def fill_images(conn: Conn, run: Run, *, limit: int = IMAGE_BATCH) -> int:

    from worker.ingest.photo import fetch_image

    with run.stage("images") as stage:
        pending = store.listings_missing_image(conn, limit=limit)
        stage.set("pending", len(pending))
        if not pending:
            return 0

        found = 0
        for listing in pending:
            image = fetch_image(str(listing["url"]))
            store.set_listing_image(conn, int(listing["id"]), image)
            if image:
                found += 1
            else:
                stage.count("no_image")
        stage.set("found", found)
        return found

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
            # Which path found this flat, so that retiring the feed can be a
            # decision about measured coverage and lead time rather than a
            # hope. The feed and the scrapers converge on one row for
            # Rightmove and Zoopla, so the row itself cannot say. See 0052.
            store.record_sightings(conn, [listing_id], FEED_READER)
            store.mark_parsed(conn, message_id, listing_id)

            # Rightmove and Zoopla both carry most London stock, so the same
            # flat arrives twice within minutes. The copy is kept for its url
            # but pointed at the original, which is what keeps it out of the
            # outbox and out of the per-district counts.
            if store.mark_duplicate(conn, listing_id) is not None:
                stage.count("duplicate")

            if listing_id not in written:
                written.append(listing_id)
            stage.count("parsed")

        stage.set("listings", len(written))
        return written

__all__ = ["BATCH", "FEED_READER", "as_listing", "run_parse"]
