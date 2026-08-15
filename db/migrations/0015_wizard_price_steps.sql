-- 0015_wizard_price_steps.sql — rent is asked as two questions, not one.
--
-- Apply after 0014.
--
-- ── what broke ───────────────────────────────────────────────────────────
--
-- 0008 wrote the wizard's step names into a CHECK constraint, which is right: a
-- typo in a step name should fail loudly rather than store a session nobody can
-- resume. The cost is that renaming a step is a migration, and splitting `price`
-- into `priceMin` and `priceMax` in the code without this one meant every wizard
-- died at step 3 — the constraint refused the save, and the person saw
-- "Something went wrong on my side" with no clue which step it was.
--
-- The constraint stays. It caught exactly the mistake it exists to catch; the fault
-- was shipping one half of the change.

BEGIN;

-- Any session mid-way through the old step becomes the first of the two, so an
-- abandoned wizard resumes at a question rather than at a name nothing recognises.
UPDATE wizard_sessions SET step = 'priceMin' WHERE step = 'price';

ALTER TABLE wizard_sessions DROP CONSTRAINT IF EXISTS wizard_step_ck;
ALTER TABLE wizard_sessions ADD CONSTRAINT wizard_step_ck CHECK (step IN (
    'overwrite', 'districts', 'bedrooms', 'priceMin', 'priceMax',
    'pets', 'furnished', 'confirm'
));

COMMIT;
