-- The bot (brief §7). `matches.why_html` is the "Why this score?" text rendered
-- by the pipeline when the match is stored, so the Worker shows exactly the
-- stored numbers and never re-derives them. `rate_limits` keeps one fixed
-- one-minute window per user for the per-user update limit.
ALTER TABLE matches ADD COLUMN why_html TEXT;

CREATE TABLE rate_limits (
    user_id      INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    window_start TEXT    NOT NULL,
    count        INTEGER NOT NULL CHECK (count >= 0)
) STRICT;
