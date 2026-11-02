-- 0073_where_a_subscriber_came_from.sql — attribution on the account itself.
--
-- Apply after 0072.
--
-- `site_visits` has answered "where did the traffic come from" since 0043, and
-- it answers it well. What it cannot answer is the question underneath it:
-- which of those posts produced a *subscriber*. The visit table counts people
-- who read the page and is deliberately unable to name any of them — the
-- visitor identity is a daily-salted hash — so there is no join from it to an
-- account, and there was never meant to be one.
--
-- So the five facts are copied onto the account at the moment the form is
-- submitted, and from then on they belong to the account. "Twelve visits from
-- reddit" and "two of this week's sign-ups came from reddit" are different
-- numbers, and only the second one says whether the post was worth making.
--
-- ── what is stored, and why these five ────────────────────────────────────
--
--   arrived_source    where they came from, normalised the same way
--                     `site_visits.source` is: 'direct', 'google', 'reddit',
--                     'facebook', or the referring host. One normaliser, so
--                     the two tables group the same way and a visit and a
--                     sign-up from the same post read as the same word.
--   arrived_referrer  the referring host alone — the evidence behind the word.
--   arrived_campaign  utm_campaign, or utm_medium when only that is given.
--   arrived_city      from the edge, as `site_visits.city` is.
--   arrived_region    the country subdivision, also from the edge.
--   arrived_language  the first tag of Accept-Language, e.g. 'en-GB'.
--
-- Prefixed rather than named `source`, `city`, `language`: `users` already has
-- `consent_source`, which is a different fact about a different thing — how
-- somebody opted in, not where they came from — and two columns a letter apart
-- is a bug waiting in whichever query reaches for the wrong one.
--
-- NOT stored, and for the same reasons 0043 gives: the IP address, the
-- coordinates the edge offers to about a city block, the full referring URL,
-- and the whole Accept-Language header. The grain here is no finer than the
-- dashboard's, which is the grain that answers the question.
--
-- ── first touch, and why it is read from the visit row ────────────────────
--
-- The form POSTs from our own page, so the request that creates the account
-- has our own domain in its Referer and reads as 'direct' — the one request
-- that cannot see where the person came from. Today's `site_visits` row can:
-- the visitor hash is computable from the same request, and that row holds the
-- *first* page of the visit, because `recordVisit` only ever fills its
-- attribution in and never overwrites it.
--
-- City, region and language are not read from there. They are properties of
-- the request itself, identical on every request of the visit, so the sign-up
-- reads its own headers for them and still has an answer when there is no
-- visit row at all — somebody on a browser we refuse to count, or a sign-up
-- made through a deployment with no visit recording.
--
-- All six nullable and NULL for every account that exists today. Nothing is
-- backfilled: there is no row to backfill from, and inventing 'direct' for
-- accounts opened before this would be a measurement rather than a gap.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS arrived_source   TEXT,
    ADD COLUMN IF NOT EXISTS arrived_referrer TEXT,
    ADD COLUMN IF NOT EXISTS arrived_campaign TEXT,
    ADD COLUMN IF NOT EXISTS arrived_city     TEXT,
    ADD COLUMN IF NOT EXISTS arrived_region   TEXT,
    ADD COLUMN IF NOT EXISTS arrived_language TEXT;

COMMENT ON COLUMN users.arrived_source IS
    'Where this account came from, normalised like site_visits.source: '
    'direct, google, reddit, facebook, or the referring host. First touch of '
    'the visit the sign-up form was submitted from. NULL where there was no '
    'visit row to read, and for every account opened before 0073.';

COMMENT ON COLUMN users.arrived_referrer IS
    'Referring host only — never the path, which can carry private context.';

COMMENT ON COLUMN users.arrived_campaign IS
    'utm_campaign from the link, or utm_medium when only that was given.';

COMMENT ON COLUMN users.arrived_city IS
    'From the edge, which knows the address without us storing it. A city is '
    'the honest grain; a coordinate would be surveillance — see 0043.';

COMMENT ON COLUMN users.arrived_region IS
    'Country subdivision from the edge — ENG, SCT and the like for the UK.';

COMMENT ON COLUMN users.arrived_language IS
    'First tag of Accept-Language, the one the browser prefers. The whole '
    'header is high-entropy and is deliberately not kept.';

-- Which posts are producing subscribers, over a window of sign-up dates. The
-- admin reads this one account at a time today, but the aggregate is the
-- question the column exists for and it is one row per account either way.
CREATE INDEX IF NOT EXISTS users_arrived_source
    ON users (created_at DESC, arrived_source);

COMMIT;
