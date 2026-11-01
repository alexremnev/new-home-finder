-- 0072_onthemarket.sql — a fifth source, read nine districts at a time.
--
-- Apply after 0071.
--
-- No column changes. This is a district reader like `rightmove`, it stores the
-- same nine filter fields every other reader does, and `property_type` already
-- had the three words it needs.
--
-- ── it has source_locations rows, unlike the last two ─────────────────────
--
-- 0061 gave SpareRoom none and said why: a region reader sweeps one pseudo-
-- district and files each listing under the outcode the card states, so the
-- district mapping has nothing to do. This reader is the other kind — it asks
-- for named outcodes — so the rows are what decide what it watches, exactly as
-- they do for Rightmove.
--
-- They are copied from `rightmove` rather than seeded fresh, so that this
-- source starts watching the districts somebody already chose instead of
-- whatever `scripts/seed_locations.py --enable` last had on its command line.
--
-- ── why nine rows cost one request ────────────────────────────────────────
--
-- Measured against the live site on 1 November 2026. The search takes up to
-- nine locations — one in the path and eight repeated as `alt-location-ids` —
-- and returns exactly what the same nine outcodes return one at a time:
--
--     nine outcodes, one query each     63 listings, 9 requests, 513KB
--     the same nine in one query        63 listings, 3 requests, 209KB
--
-- compared as sets of listing ids, not as counters, with nothing in either
-- difference. A tenth location is answered with HTTP 200, `totalResults: null`
-- and an empty list — no error of any kind — which is why the reader re-reads
-- `query.altLocationIds` off every page it parses and refuses the page rather
-- than reporting an empty district. See `onthemarket.honoured`.
--
-- So the whole of zones 1-3 is 101 districts in 14 requests and 954KB, and
-- widening coverage to the city is an UPDATE here and no second job: there is
-- deliberately no `onthemarket_london` beside this, which is the one way it
-- differs from Zoopla.
--
-- ── the window is the completeness guarantee ──────────────────────────────
--
-- robots.txt disallows `*sort-field=**`, so the site's own "Recent" ordering
-- is out of bounds — and measured once, it did not order by arrival anyway:
-- paid slots sit above it and the field is the update date. Unfiltered,
-- `/to-rent/property/london/` reports 35,822 rentals against a 1,000-result
-- paging ceiling in the page's own globals.
--
-- `recently-added=24-hours` is not disallowed and is read instead. It makes
-- this the only reader here with no watermark to reach: a run either read the
-- window or it did not, a missed run costs nothing because the next one reads
-- the same 24 hours, and an outage longer than the window is what
-- `sweep.GAP_HOURS` already voids the watch for.
--
-- ── min_items is 10 ───────────────────────────────────────────────────────
--
-- The health gate floor, and it is a figure about the batch rather than the
-- page: one page carries 30 cards, and a batch of nine subscribed outcodes
-- returned 10 to 40 listings across three measured batches on 1 November 2026
-- with a quiet batch of nine outer districts at 9. Ten is therefore "a batch
-- came back with something", low enough that a quiet Sunday does not trip it.
--
-- ── proxy is left on escalation, which is a decision and not a default ─────
--
-- Nothing in the headers but nginx behind CloudFront — no Cloudflare, no
-- DataDome, no Akamai — and a plain `curl/8.7.1` with no cookies and no TLS
-- impersonation was served 200 on every page, which is the opposite of Zoopla.
-- So this probably costs nothing. Whether the *server's* address is served is
-- untested, exactly as it was for SpareRoom; if it is refused the scrape stage
-- says so in `refused_this_address` and the answer is to read that and decide.
--
-- What must NOT happen is paying for a 404. This origin sheds load as a bare
-- nginx 404 — 7 of 12 deep pages on the whole-London query — and `fetch.get`
-- is asked to `tolerate` it so that no second fingerprint and no proxy exit is
-- spent being told the same thing again. `proxy_bytes` above zero on this
-- reader is therefore news, and most likely means that handling has broken
-- rather than that the portal started blocking us.

BEGIN;

INSERT INTO sources (key, display_name, enabled, min_items, config) VALUES (
    'onthemarket', 'OnTheMarket', true, 10,
    '{
       "reader": "worker.sources.onthemarket",
       "discovery": "district batches of 9 over recently-added=24-hours, cards from __NEXT_DATA__",
       "respect_robots": true,
       "batch": 9,
       "recently_added": "24-hours",
       "page_size": 30,
       "max_pages": 8,
       "detail_budget": 40,
       "resolve_budget": 15,
       "proxy": {"enabled": true, "pinned_direct": false, "expected_spend": "none"},
       "notes": "robots.txt forbids sort-field, direction, exclusive-first and every price and bedroom filter, so there is no newest-first read; recently-added=24-hours is permitted and is what makes the window its own completeness guarantee. Nine locations per request, measured, and a tenth returns an empty page with HTTP 200 — the reader re-checks query.altLocationIds and query.recentlyAdded on every page. Search pages are cached by CloudFront for 1800s and no request header overrides it, so the latency floor is the TTL and not the timer. Full postcode is on the listing page in headerData.dataLayer, stated on 14 of 14 pages read; furnishing, availability, minimum term and floor area come from the same page. About 8% of cards end in neither an outcode nor a postcode and have their district read from that page once, remembered in seen_ids."
     }'::jsonb
)
ON CONFLICT (key) DO UPDATE
   SET display_name = EXCLUDED.display_name,
       enabled      = EXCLUDED.enabled,
       min_items    = EXCLUDED.min_items,
       config       = EXCLUDED.config;

-- The same districts Rightmove is set to watch, enabled the same way. A fresh
-- database with no rightmove rows yet gets nothing here and is seeded by
-- `scripts/seed_locations.py`, which already writes a row per district for
-- every enabled source.
INSERT INTO source_locations (source_key, location_id, external_id, enabled)
SELECT 'onthemarket', sl.location_id, lower(l.code), sl.enabled
  FROM source_locations sl
  JOIN locations l ON l.id = sl.location_id
 WHERE sl.source_key = 'rightmove'
   AND l.kind = 'postcode_district'
ON CONFLICT (source_key, location_id) DO NOTHING;

COMMIT;
