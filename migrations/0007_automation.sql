-- Automation (brief §10.5). A sender claims the notifications it is about to
-- send with a lease, so two overlapping pipeline runs (a scheduled run and a
-- user's backfill, say) can never send the same row twice. A lease that runs
-- out (the run died) frees the row for the next run.
ALTER TABLE notifications ADD COLUMN lease_id TEXT;
ALTER TABLE notifications ADD COLUMN lease_until TEXT;

-- Years of experience read from a CV. Kept on the profile, which the pipeline
-- owns, so a CV parsed before setup finishes isn't lost; an explicit value in
-- user_prefs wins.
ALTER TABLE user_profiles ADD COLUMN years_experience REAL
    CHECK (years_experience IS NULL OR years_experience >= 0);
