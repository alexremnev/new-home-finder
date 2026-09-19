-- 0030_drop_support_tickets.sql — support is an email address now.
--
-- Apply after 0029.
--
-- 0025 built a ticket queue: the bot asked for the complaint, then for an email,
-- and the admin had a tab to read and close what came in. That is a helpdesk,
-- and nobody staffs it — /support now answers with the address and nothing is
-- stored. The table, its indexes and its constraints go with the feature.
--
-- This drops what was already collected. Take a copy first if any of it is
-- still wanted:
--
--     \copy (SELECT * FROM support_tickets) TO 'tickets.csv' WITH CSV HEADER
--
-- The indexes and the RLS policy belong to the table and go with it; naming
-- them here would only be a list to keep in step with 0025.

BEGIN;

DROP TABLE IF EXISTS support_tickets;

COMMIT;
