-- 0002_rightmove.sql — add Rightmove as a second source.
--
-- Zoopla is deliberately absent: its listing pages are behind an interactive
-- challenge, so there is no implementation path that does not defeat it.
--
-- One difference from OpenRent shapes the discovery config below. An OpenRent
-- listing URL carries its outward code, so a district scope can be applied before
-- any page is fetched. A Rightmove listing URL is `/properties/<id>` and carries
-- no location at all, so the district is known either from the search query or
-- from the address on the search result card. Until each district's Rightmove
-- location identifier is filled into source_locations.external_id, discovery
-- searches London-wide and filters on the card address, which costs one large
-- search page per run rather than one per district.

BEGIN;

INSERT INTO sources (key, display_name, enabled, min_items, config) VALUES (
    'rightmove', 'Rightmove', true, 5,
    '{
       "user_agent_mode": "auto",
       "impersonate": "chrome124",
       "rate_limit_rps": 0.2,
       "sweep_rate_limit_rps": 0.1,
       "concurrency": 1,
       "shuffle_urls": true,
       "respect_robots": true,
       "conditional_requests": true,
       "jitter": {"between_requests": "exponential", "min_factor": 0.4, "max_factor": 3.0},
       "retry": {"max_attempts": 3, "base_seconds": 8, "max_seconds": 180},
       "breaker": {"on": ["403", "503", "challenge"],
                   "cooldown_base_minutes": 60, "cooldown_max_hours": 24},
       "proxy": {"enabled": false, "provider": null},
       "discovery": {
         "primary": {"kind": "search_page", "enabled": true,
                     "url": "https://www.rightmove.co.uk/property-to-rent/London.html",
                     "scope_from": "card_address",
                     "note": "set source_locations.external_id to the site location identifier to search per district instead"},
         "fallback": {"kind": "sitemap_diff", "enabled": false}
       }
     }'::jsonb
);

-- Slower than OpenRent and with a longer cooldown: this source is less tolerant,
-- so the run visits less often and backs off for longer after a refusal.
INSERT INTO schedules (job, source_key, interval_seconds, jitter_pct, max_requests, quiet_hours)
VALUES
    ('hot',   'rightmove', 2700, 30, 80,
     '{"tz": "Europe/London", "from": "23:00", "to": "07:00", "mode": "queue"}'::jsonb),
    ('sweep', 'rightmove', 86400, 20, 400, '{}'::jsonb);

-- Same district rows as OpenRent, enabled wherever OpenRent is enabled so the
-- two sources cover the same area by default. external_id holds the outward code
-- as a placeholder; see the note above.
INSERT INTO source_locations (source_key, location_id, external_id, enabled)
SELECT 'rightmove', l.id, lower(l.code),
       coalesce((SELECT sl.enabled FROM source_locations sl
                  WHERE sl.source_key = 'openrent' AND sl.location_id = l.id), false)
  FROM locations l
 WHERE l.kind = 'postcode_district'
ON CONFLICT (source_key, location_id) DO NOTHING;

COMMIT;