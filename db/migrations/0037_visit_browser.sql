-- 0037_visit_browser.sql — which browser a visit came from.
--
-- Apply after 0036.
--
-- Two questions at once, and the second is the reason this is worth a column.
--
-- The obvious one: "which browser", asked of any traffic number.
--
-- The useful one: whether it was a person at all. The user-agent blocklist can
-- only catch what names itself — "bot", "crawl", "curl" — and most automation
-- now sends a plain Chrome string or something that names no browser whatever.
-- Turning the question around is far stronger: if nothing recognisable as a
-- browser is in the string, it was not somebody reading the page. So a visit
-- with no identifiable browser is no longer recorded, and this column is what
-- that decision is made against.
--
-- Deliberately coarse. "Chrome" and not "Chrome 141.0.7390.55": a version
-- number is a fingerprint, and the question was which browser.
--
-- Nullable, because rows written before this have no answer and guessing one
-- would be worse than admitting it.

BEGIN;

ALTER TABLE site_visits
    ADD COLUMN IF NOT EXISTS browser TEXT;

CREATE INDEX IF NOT EXISTS site_visits_browser ON site_visits (day DESC, browser);

COMMIT;
