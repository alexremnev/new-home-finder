-- 0003_rls.sql — close the public API surface.
--
-- Supabase publishes a REST endpoint over every table in the `public` schema,
-- reachable with the project's anon key. That key is designed to be shipped in
-- front-end code, so without row level security it grants read and write access
-- to `users`, `user_channels`, `subscriptions`, and `notifications` — personal
-- data, to anyone who has the project URL.
--
-- Nothing in this system needs that endpoint. The worker connects over Postgres
-- as the table owner and the web routes are server-side, and both bypass row
-- level security. Enabling it with no policies therefore removes the exposure
-- without affecting anything that legitimately reads these tables.
--
-- Kept as a migration rather than a click in the dashboard so that a rebuilt
-- project ends up in the same state.

BEGIN;

ALTER TABLE sources            ENABLE ROW LEVEL SECURITY;
ALTER TABLE channels           ENABLE ROW LEVEL SECURITY;
ALTER TABLE locations          ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_locations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE parse_schemas      ENABLE ROW LEVEL SECURITY;
ALTER TABLE listings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE listing_price_log  ENABLE ROW LEVEL SECURITY;
ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_channels      ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications      ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedules          ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_runs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_stages         ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_events         ENABLE ROW LEVEL SECURITY;

-- No policies are created. With row level security on and no policy present,
-- these roles match no rows, which is the intended outcome. A policy should be
-- added only if a browser is ever given direct access to a specific table.

-- Belt and braces: revoke the grants those roles receive by default, so a table
-- created later without row level security is not exposed by omission.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;

ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

COMMIT;

-- Verification: every table should report rowsecurity = true.
--
--   SELECT tablename, rowsecurity
--     FROM pg_tables
--    WHERE schemaname = 'public'
--    ORDER BY rowsecurity, tablename;
--
-- And the anon role should hold no table privileges:
--
--   SELECT count(*) AS anon_grants
--     FROM information_schema.role_table_grants
--    WHERE grantee = 'anon' AND table_schema = 'public';
