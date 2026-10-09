-- 0061_spareroom.sql — the flatshare site, read as a rotating window.
--
-- Apply after 0060.
--
-- A fourth portal reader, and the first whose stock is mostly rooms in shared
-- homes rather than whole properties. `property_type` already had the word for
-- that — 'room', beside 'flat' and 'house' — so no column changes.
--
-- ── no source_locations row, on purpose ───────────────────────────────────
--
-- This is a region reader. `worker.sources.sweep.collect` sweeps the pseudo-
-- district named in `Portal.regions` and then files each listing under the
-- outcode the card itself states, so the district mapping that
-- `source_locations` exists for has nothing to do here. `zoopla_london` got no
-- row either, for the same reason.
--
-- The reason it has to be a region reader is worth recording in the database
-- as well as in the module, because it is the thing somebody will try to
-- "fix": SpareRoom has no page for an outcode. `/flatshare/london/se16`
-- answers 302 into `/flatshare/search.pl?…&action=search`, and that path is
-- disallowed by their robots.txt. Its crawlable geography is area names —
-- Bermondsey, Rotherhithe — and an area is not an outcode: the Bermondsey page
-- on 8 October 2026 held 6 cards in SE1 and 5 in SE16.
--
-- ── min_items is 10 and not 5 ─────────────────────────────────────────────
--
-- The health gate floor. One search page carries exactly ten listings and this
-- reader reads six pages a run, so a healthy run sees about sixty. Ten is
-- therefore "one page came back" — low enough that a quiet weekend run does
-- not trip it, high enough that a page which has stopped yielding cards does.
--
-- ── proxy.enabled is false and the reader pins it ─────────────────────────
--
-- Recorded here for the operator, enforced in code. `SpareRoom.direct_only`
-- names the host, and `sweep._pin_direct` adds it to the fetcher's direct
-- list, so this reader cannot escalate to the metered residential proxy even
-- on a server where SCRAPE_PROXY is set.
--
-- That is a deliberate decision rather than a default: measured from a desk on
-- 8 and 9 October 2026 SpareRoom serves every page 200 under chrome124, with
-- Apache behind Google's load balancer and Fastly and no Cloudflare, DataDome
-- or Akamai anywhere in the headers — so there is no fingerprint wall of the
-- kind that made Zoopla and OpenRent cost money. Whether the *server's*
-- address is served is untested. If it is refused, the scrape stage says so in
-- `refused_and_pinned` and the run log carries a warn; the answer is to read
-- that and decide, not to start paying without noticing.

BEGIN;

INSERT INTO sources (key, display_name, enabled, min_items, config) VALUES (
    'spareroom', 'SpareRoom', true, 10,
    '{
       "reader": "worker.sources.spareroom",
       "discovery": "london area pages, data-listing-* attributes on each card",
       "impersonate": "chrome124",
       "respect_robots": true,
       "region": "london",
       "window_pages": 60,
       "pages_per_run": 6,
       "detail_budget": 15,
       "proxy": {"enabled": false, "pinned_direct": true},
       "notes": "robots.txt forbids search.pl, api.pl, every filter query parameter and sort_by=days_since_placed, so the feed cannot be read newest-first; the reader rotates a 60-page window instead. No full postcode is published anywhere, so postcodes are derived from the advert page coordinates and marked derived. bathrooms and floor_area_sqft do not exist on this site at all."
     }'::jsonb
)
ON CONFLICT (key) DO UPDATE
   SET display_name = EXCLUDED.display_name,
       enabled      = EXCLUDED.enabled,
       min_items    = EXCLUDED.min_items,
       config       = EXCLUDED.config;

COMMIT;
