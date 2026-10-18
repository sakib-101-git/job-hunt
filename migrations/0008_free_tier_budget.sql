-- Keep D1 within its free daily limits (rows read and written), which count
-- every row a query touches and every index entry it changes.
--
-- jobs.record_hash: a hash of what a source said about a job, so a listing seen
--   again unchanged is not written again (last_seen_at is refreshed at most
--   once per normalization.last_seen_refresh_hours).
-- jobs.scored_at / user_prefs.scored_at: when a job was scored against every
--   user, and a user against every open job, so each run scores only new or
--   re-gated jobs and users whose settings changed.
-- users.last_sent_at: when the user was last sent something (the daily
--   digest's clock), instead of scanning their notifications every run.
ALTER TABLE jobs ADD COLUMN record_hash TEXT;
ALTER TABLE jobs ADD COLUMN scored_at TEXT;
ALTER TABLE user_prefs ADD COLUMN scored_at TEXT;
ALTER TABLE users ADD COLUMN last_sent_at TEXT;

CREATE INDEX jobs_to_score ON jobs (id) WHERE status = 'open' AND scored_at IS NULL;
