-- 0051_portal_scrapers.sql — the three portals read from their own pages.
--
-- `rightmove` and `openrent` already have rows. This adds `openrent_v2` and
-- turns `zoopla` on, so that all three of the new readers have somewhere to
-- record what they have swept.
--
-- ── why openrent_v2 is a separate source and not a replacement ────────────
--
-- The original OpenRent reader discovers listings from the nationwide sitemap;
-- the new one reads district search pages, which is about fifty times less
-- traffic. Rather than switch one for the other and hope, both exist: they can
-- run side by side, and `listings` will show which finds what.
--
-- A separate key means the same flat can be stored twice, once per reader.
-- That is handled and not a problem: the duplicate rule in 0040 matches on
-- postcode, price, bedrooms and bathrooms across *different* sources, so the
-- second copy is marked as a duplicate of the first and never announced twice.
-- Whichever reader saw it first is the one that reports it.
--
-- When the new one has proven itself, disabling the old is one UPDATE and the
-- deletion of a timer; nothing in the worker needs changing.

INSERT INTO sources (key, display_name, enabled, min_items, config) VALUES (
    'openrent_v2', 'OpenRent (search pages)', true, 5,
    '{
       "reader": "worker.sources.openrent_v2",
       "discovery": "district search page, PROPERTYIDS",
       "impersonate": "chrome124",
       "respect_robots": true,
       "notes": "robots.txt permits /properties-to-rent/; the 2km search radius means the slug decides the district, not the search"
     }'::jsonb
)
ON CONFLICT (key) DO UPDATE
   SET display_name = EXCLUDED.display_name,
       enabled      = EXCLUDED.enabled,
       config       = EXCLUDED.config;

-- Zoopla was registered disabled in 0012 because nothing could read it. There
-- is a reader now: it refuses a plain client outright — it answered 403 even
-- to robots.txt from urllib — and serves the same request under TLS
-- impersonation, which is what the shared transport does.
UPDATE sources
   SET enabled = true,
       display_name = 'Zoopla',
       config = coalesce(config, '{}'::jsonb) || '{
         "reader": "worker.sources.zoopla",
         "discovery": "district search page, regularListingsFormatted",
         "impersonate": "chrome124",
         "respect_robots": true,
         "notes": "featuredListingsFormatted is promoted and extendedListingsFormatted is outside the district; neither is read"
       }'::jsonb
 WHERE key = 'zoopla';

UPDATE sources
   SET config = coalesce(config, '{}'::jsonb) || '{
         "reader": "worker.sources.rightmove",
         "discovery": "district search page, __NEXT_DATA__",
         "sort": "sortType=6, ordered by listingUpdateDate"
       }'::jsonb
 WHERE key = 'rightmove';
