-- 0012_portal_sources.sql — the portals a feed's listings actually belong to.
--
-- Apply after 0011.
--
-- ── why these rows are needed ────────────────────────────────────────────
--
-- A listing that arrives through the Telegram feed is written under the portal that
-- hosts it, not under the feed: `source_key = 'zoopla'`, `external_id = '73991134'`.
-- That is what makes `UNIQUE (source_key, external_id)` deduplicate the same
-- listing against our own scraper later, for free.
--
-- `listings.source_key` references `sources`, so every portal the feed can name has
-- to be a row. `rightmove` and `openrent` already are, because they are scraped;
-- `zoopla` was not, and the first zoopla listing to arrive failed the foreign key.
--
-- ── why `enabled = false` ────────────────────────────────────────────────
--
-- `enabled` governs *scraping*: the scheduler picks up enabled sources and fetches
-- their pages. Zoopla is not scraped — the specification records that its terms
-- forbid it — and nothing here changes that. The row exists so a listing learned
-- from the feed has somewhere to live, and false is what keeps the scheduler away
-- from it.
--
-- Note that `active_subscriptions` and `enabledDistricts()` join `sources` on
-- `enabled`, so coverage still comes from the enabled sources only. This row adds a
-- destination for listings, not a scope for subscriptions.

BEGIN;

INSERT INTO sources (key, display_name, enabled, min_items, config)
VALUES ('zoopla', 'Zoopla', false, 0,
        '{"kind": "portal_reference",
          "note": "not scraped; listings arrive via a feed and are keyed to this portal"}'::jsonb)
ON CONFLICT (key) DO NOTHING;

COMMIT;
