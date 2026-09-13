-- 0021_drop_schedules.sql — the schedule table is gone.
--
-- Apply after 0020, and only after the deploy that removed the code.
--
-- 0001 built a scheduler inside the worker: `schedules` held a row per job with an
-- interval, jitter and quiet hours, and `python -m worker tick` read it to decide
-- what was due. It was the right shape for a scraper that had to pace itself
-- against somebody else's website.
--
-- Nothing paces itself now. The feed is read as fast as it is written, and what
-- decides when a job runs is the host's timer — Task Scheduler, a cron entry, or
-- scripts/loop.sh. Two timers deciding the same thing is one too many, and the one
-- in the database was the one nothing consulted.
--
-- Removed with it: the `tick` and `schedules` commands, `_execute`, `_quiet_state`,
-- `_parse_time`, `_show_schedules`, and `claim_schedule`, `fetch_due_schedules` and
-- `finish_schedule` from worker/db.py.

BEGIN;

DROP TABLE IF EXISTS schedules;

COMMIT;
