-- 0052_who_saw_it.sql — which reader saw a listing, so the feed can be retired
-- on evidence instead of on a guess.
--
-- ── why this cannot be answered from `listings` ───────────────────────────
--
-- `listings` is keyed UNIQUE (source_key, external_id), and the Telegram feed
-- and the Rightmove scraper extract the *same* id for the same flat — the feed
-- from `rightmove.co.uk/properties/93625473`, the scraper from the search
-- page's `id` field. So `insert_listing` upserts and the two paths converge on
-- one row.
--
-- That is exactly what we want for delivery: `notifications` is UNIQUE
-- (user_id, listing_id), so whichever path got there first sends the alert and
-- the second changes nothing. But it also means the row remembers nothing
-- about who found it, and "would the scraper have caught everything the feed
-- caught" is unanswerable.
--
-- OpenRent is the other shape: the old and new readers use different source
-- keys, so the same flat is two rows. They share the numeric id, which is why
-- the comparison below groups by external_id rather than by listing id.
--
-- ── what a sighting is ───────────────────────────────────────────────────
--
-- One row the first time a reader sees a listing, and nothing after that.
-- `first_at` is when that reader first saw it, which is the whole point: the
-- question is not only whether the scraper finds what the feed finds, but
-- whether it finds it as fast. For an alerting product a reader that is
-- complete but five minutes slower is not a replacement.
--
-- Written by every reader on every run, for every listing it sees — not only
-- the ones new to us. A reader that keeps re-seeing a listing somebody else
-- stored first is the interesting case, and skipping it would make the scraper
-- look like it had missed everything the feed got in first.

CREATE TABLE IF NOT EXISTS listing_sightings (
    listing_id BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    -- 'tg_feed' for the Telegram feed; otherwise the source key of the
    -- scraper: 'rightmove', 'zoopla', 'openrent_v2', 'openrent'.
    reader     TEXT   NOT NULL,
    first_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, reader)
);

COMMENT ON TABLE listing_sightings IS
    'Which reader saw which listing, and when it first did. Exists so that '
    'retiring the Telegram feed can be a decision about measured coverage and '
    'lead time rather than a hope.';

-- The panel reads "everything seen in this window, by reader", so the index
-- follows the time rather than the listing.
CREATE INDEX IF NOT EXISTS listing_sightings_when
    ON listing_sightings (first_at DESC);
CREATE INDEX IF NOT EXISTS listing_sightings_reader
    ON listing_sightings (reader, first_at DESC);

ALTER TABLE listing_sightings ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON listing_sightings FROM %I', target);
        END IF;
    END LOOP;
END $$;

-- Everything already stored was seen before any of this was recorded, and
-- guessing who saw it would put made-up evidence into the one table whose
-- whole purpose is evidence. So nothing is backfilled: the comparison simply
-- has no history before today, and the panel says so.
