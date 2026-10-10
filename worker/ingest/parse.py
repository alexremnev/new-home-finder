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
    # reader that owns that shape rather than copied.
    #
    # The same slug also names the property type, and the feed states none at
    # all: its Bedrooms field says "2 Bedrooms" and stops, so `bedrooms_of`
    # returns a type only for a studio or a room. An untyped listing passes
    # every property-type filter — `match._check_property_type` lets silence
    # through, which is right — so somebody who asked for a flat was being sent
    # whatever OpenRent had, houses and rooms included. The slug is the cheapest
    # possible fix: no request, and `read_slug` already reads it.
    property_type = parsed.property_type
    if parsed.source_key == "openrent" and (not raw.get("address") or not property_type):
        from worker.sources.openrent import place_in_slug, read_slug

        slug = parsed.url.rstrip("/").rsplit("/", 2)[-2] if "/" in parsed.url else ""
        if not raw.get("address"):
            raw["address"] = place_in_slug(slug) or ""
        if not property_type:
            read = read_slug(slug)
            # Whatever the message said wins; this only fills a blank.
            property_type = read[2] if read else None

    return Listing(
        source_key=parsed.source_key,
        external_id=parsed.external_id,
        url=parsed.url,
        price_pcm=parsed.price_pcm,
        bedrooms=parsed.bedrooms,
        bathrooms=parsed.bathrooms,
        property_type=property_type,
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

# The same bound for the same reason, and deliberately a second budget rather
# than a share of the first: the two steps mostly want different listings. The
# image step skips anything whose feed message carried a photograph, and those
# are exactly the listings that still need a type.
TYPE_BATCH = 12

# One run's worth of listing pages, keyed by url. Both fill steps read facts
# out of the same document — og:image and the title — so a listing that needs
# both is fetched once. A failure is remembered as an empty page so that it is
# not retried within the run either.
Pages = dict[str, str]


def _page(url: str, pages: Pages | None) -> str | None:
    from worker.ingest.photo import head_of

    if pages is not None and url in pages:
        return pages[url] or None
    page = head_of(url)
    if pages is not None:
        pages[url] = page or ""
    return page


def fill_images(
    conn: Conn, run: Run, *, limit: int = IMAGE_BATCH, pages: Pages | None = None
) -> int:

    from worker.ingest.photo import image_in

    with run.stage("images") as stage:
        pending = store.listings_missing_image(conn, limit=limit)
        stage.set("pending", len(pending))
        if not pending:
            return 0

        found = 0
        for listing in pending:
            page = _page(str(listing["url"]), pages)
            image = image_in(page) if page else None
            store.set_listing_image(conn, int(listing["id"]), image)
            if image:
                found += 1
            else:
                stage.count("no_image")
        stage.set("found", found)
        return found


def fill_types(
    conn: Conn, run: Run, *, limit: int = TYPE_BATCH, pages: Pages | None = None
) -> int:
    """Read the property type off the listing page, for listings that have none.

    Only the feed produces those — it states no type at all — and an untyped
    listing both reads worse in an alert and answers every property-type
    filter. The page says what it is in its own title; see
    `worker.ingest.kind`, which also records why Zoopla cannot be read this
    way.
    """

    from worker.ingest.kind import type_in

    with run.stage("types") as stage:
        pending = store.listings_missing_type(conn, limit=limit)
        stage.set("pending", len(pending))
        if not pending:
            return 0

        found = 0
        for listing in pending:
            page = _page(str(listing["url"]), pages)
            kind = type_in(page) if page else None
            store.set_listing_type(conn, int(listing["id"]), kind)
            if kind:
                found += 1
            else:
                stage.count("no_type")
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

__all__ = [
    "BATCH", "FEED_READER", "IMAGE_BATCH", "TYPE_BATCH", "as_listing",
    "fill_images", "fill_types", "run_parse",
]
