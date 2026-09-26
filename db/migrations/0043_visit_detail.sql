-- 0043_visit_detail.sql — where a visitor actually came from.
--
-- Apply after 0042.
--
-- The page counted visitors and knew their country, device and browser. It did
-- not know the one thing worth knowing: how they arrived. "Twelve people from
-- the United Kingdom on Chrome" does not say whether the money spent on a post
-- did anything.
--
-- ── what a request can tell us, and what is stored ───────────────────────
--
-- Stored:
--   source     where they came from, normalised — 'direct', 'google',
--              'instagram', or the referring host. utm_source wins over the
--              header when both are present, because that is the campaign
--              saying what it is.
--   referrer   the referring host alone. The evidence behind `source`.
--   campaign   utm_campaign, or utm_medium when only that is given.
--   city       from the edge.
--   region     the country subdivision, also from the edge.
--   os         read from the user agent.
--   language   the first tag of Accept-Language, e.g. 'en-GB'.
--
-- Deliberately NOT stored, though the request offers them:
--   the IP address — the whole visitor identity here is a daily-salted hash so
--     that people can be counted without any of them being followed. Keeping
--     the address would undo that, and the country already answers "where".
--   latitude and longitude — the edge gives them to about a city block. A city
--     is the honest grain for a dashboard; a coordinate is surveillance.
--   the full referring URL — a path can carry someone's private context (a
--     search query, a group name). The host answers the question.
--   the full Accept-Language and the sec-ch-ua-* client hints — high-entropy
--     strings whose only use beyond what is stored here is fingerprinting.
--
-- Robots are excluded before any of this is written, and that is unchanged:
-- `recordVisit` refuses a request whose user agent names a robot, and then
-- refuses any request with no recognisable browser at all.

BEGIN;

ALTER TABLE site_visits
    ADD COLUMN IF NOT EXISTS source   TEXT,
    ADD COLUMN IF NOT EXISTS referrer TEXT,
    ADD COLUMN IF NOT EXISTS campaign TEXT,
    ADD COLUMN IF NOT EXISTS city     TEXT,
    ADD COLUMN IF NOT EXISTS region   TEXT,
    ADD COLUMN IF NOT EXISTS os       TEXT,
    ADD COLUMN IF NOT EXISTS language TEXT;

COMMENT ON COLUMN site_visits.source IS
    'Where the visit came from, normalised: direct, google, instagram, or the '
    'referring host. utm_source wins over the Referer header.';

COMMENT ON COLUMN site_visits.referrer IS
    'Referring host only — never the path, which can carry private context.';

COMMENT ON COLUMN site_visits.os IS
    'Guessed from the user agent, which is a claim rather than a fact.';

-- Every row written before this migration has NULL in all seven, and the page
-- shows those as "unknown" rather than pretending otherwise.

COMMIT;
