-- 0004_source_failures.sql — count consecutive refusals per source.
--
-- The circuit breaker is meant to hold a source out for longer after each
-- successive refusal, which needs a count of them. `schedules.consecutive_fails`
-- already exists but tracks something else: whether a scheduled job completed.
-- A source can be refused while the job itself finishes cleanly, and the same
-- source can be reached by several schedules, so the two must not share a counter.
--
-- Without this column the escalation silently did not happen: the cooldown was
-- always the base value, however many times in a row the source refused us.

BEGIN;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS consecutive_fails SMALLINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN sources.consecutive_fails IS
    'Refusals in a row. Doubles the circuit-breaker cooldown; reset by a clean run.';

COMMIT;
