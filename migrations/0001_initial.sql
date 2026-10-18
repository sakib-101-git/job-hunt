-- JobHunt v2 schema. Applied by `wrangler d1 migrations apply` in production and
-- by `jobhunt.db.apply_migrations` for local SQLite, from these same files.
--
-- Conventions: STRICT tables; timestamps are ISO 8601 UTC text
-- ("2026-10-18T09:30:00Z"); *_json columns hold valid JSON; booleans are 0/1.
-- Defaults that are product settings (min score, delivery mode, ...) are not
-- set here: the application writes them from config/.

CREATE TABLE users (
    id               INTEGER PRIMARY KEY,  -- Telegram user id
    chat_id          INTEGER NOT NULL,
    username         TEXT,
    first_name       TEXT,
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'paused', 'blocked', 'deleted')),
    paused_until     TEXT,
    onboarding_state TEXT    CHECK (onboarding_state IS NULL OR json_valid(onboarding_state)),
    locale           TEXT    NOT NULL,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE INDEX users_status ON users (status);

CREATE TABLE user_prefs (
    user_id               INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    tracks_json           TEXT    NOT NULL CHECK (json_valid(tracks_json)),
    domain_overrides_json TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(domain_overrides_json)),
    include_adjacent      INTEGER NOT NULL CHECK (include_adjacent IN (0, 1)),
    experience_level      TEXT    NOT NULL
                          CHECK (experience_level IN
                                 ('student_intern', 'fresh_grad', 'junior', 'mid', 'senior')),
    years_experience      REAL    CHECK (years_experience IS NULL OR years_experience >= 0),
    locations_json        TEXT    NOT NULL CHECK (json_valid(locations_json)),
    preferred_cities_json TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(preferred_cities_json)),
    job_types_json        TEXT    NOT NULL CHECK (json_valid(job_types_json)),
    min_score             REAL    NOT NULL CHECK (min_score BETWEEN 0 AND 10),
    delivery_mode         TEXT    NOT NULL CHECK (delivery_mode IN ('instant', 'digest')),
    quiet_hours_json      TEXT    CHECK (quiet_hours_json IS NULL OR json_valid(quiet_hours_json)),
    max_per_cycle         INTEGER NOT NULL CHECK (max_per_cycle > 0),
    updated_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE user_profiles (
    user_id         INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    skills_json     TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(skills_json)),
    education_json  TEXT    CHECK (education_json IS NULL OR json_valid(education_json)),
    summary         TEXT,
    cv_file_id      TEXT,
    cv_parsed_at    TEXT,
    profile_version INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE jobs (
    id                          INTEGER PRIMARY KEY,
    source                      TEXT NOT NULL,
    source_job_id               TEXT NOT NULL,
    url                         TEXT NOT NULL,
    canonical_url               TEXT NOT NULL,
    fingerprint                 TEXT NOT NULL,  -- sha1(company | title | city), cross-source
    title                       TEXT NOT NULL,
    company                     TEXT,
    location_raw                TEXT,
    remote_eligibility_evidence TEXT,           -- quote or field that made a remote job eligible
    posted_at                   TEXT,
    posted_at_source            TEXT NOT NULL
                                CHECK (posted_at_source IN ('api', 'rss', 'text', 'unknown')),
    deadline                    TEXT,
    jd_text                     TEXT,           -- cleared by retention; job_facts is kept
    jd_hash                     TEXT,
    first_seen_at               TEXT NOT NULL,
    last_seen_at                TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'open'
                                CHECK (status IN ('open', 'expired', 'filtered')),
    filter_reason               TEXT,
    UNIQUE (source, source_job_id)
) STRICT;

CREATE INDEX jobs_fingerprint ON jobs (fingerprint);
CREATE INDEX jobs_status_first_seen ON jobs (status, first_seen_at);
CREATE INDEX jobs_last_seen ON jobs (last_seen_at);

CREATE TABLE job_domains (
    job_id      INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    domain_slug TEXT    NOT NULL,
    confidence  REAL    NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    method      TEXT    NOT NULL CHECK (method IN ('exact', 'alias', 'fuzzy', 'llm')),
    is_primary  INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (job_id, domain_slug)
) STRICT, WITHOUT ROWID;

CREATE INDEX job_domains_slug ON job_domains (domain_slug);
CREATE UNIQUE INDEX job_domains_one_primary ON job_domains (job_id) WHERE is_primary = 1;

CREATE TABLE job_facts (
    job_id                  INTEGER PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    facts_json              TEXT    NOT NULL CHECK (json_valid(facts_json)),
    provider                TEXT    NOT NULL,
    model                   TEXT    NOT NULL,
    prompt_version          TEXT    NOT NULL,
    input_hash              TEXT    NOT NULL,
    grounding_ok            INTEGER NOT NULL CHECK (grounding_ok IN (0, 1)),
    grounding_failures_json TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(grounding_failures_json)),
    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE matches (
    id                INTEGER PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    job_id            INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    score             REAL    NOT NULL CHECK (score BETWEEN 0 AND 10),
    breakdown_json    TEXT    NOT NULL CHECK (json_valid(breakdown_json)),
    explanation_json  TEXT    NOT NULL CHECK (json_valid(explanation_json)),
    gate_results_json TEXT    NOT NULL CHECK (json_valid(gate_results_json)),
    scorer_version    TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (user_id, job_id)
) STRICT;

CREATE INDEX matches_job ON matches (job_id);

-- One row per (user, job fingerprint): the same job found on two boards can
-- never reach a user twice, even across overlapping pipeline runs.
CREATE TABLE notifications (
    id                  INTEGER PRIMARY KEY,
    match_id            INTEGER NOT NULL UNIQUE REFERENCES matches (id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    job_id              INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    fingerprint         TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    telegram_message_id INTEGER,
    attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error          TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    sent_at             TEXT,
    UNIQUE (user_id, fingerprint)
) STRICT;

CREATE INDEX notifications_pending ON notifications (created_at) WHERE status = 'pending';
CREATE INDEX notifications_job ON notifications (job_id);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    match_id   INTEGER NOT NULL REFERENCES matches (id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL
               CHECK (kind IN ('applied', 'saved', 'hidden', 'thumbs_up', 'thumbs_down')),
    reason     TEXT,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (match_id, kind)
) STRICT;

CREATE INDEX feedback_user_kind ON feedback (user_id, kind);

CREATE TABLE source_state (
    source               TEXT PRIMARY KEY,
    cursor               TEXT,
    etag                 TEXT,
    last_modified        TEXT,
    next_run_at          TEXT,
    last_success_at      TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    consecutive_empty    INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_empty >= 0),
    last_count           INTEGER,
    bootstrapped         INTEGER NOT NULL DEFAULT 0 CHECK (bootstrapped IN (0, 1)),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    trigger     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    stats_json  TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stats_json))
) STRICT;

CREATE INDEX runs_started ON runs (started_at);

CREATE TABLE llm_usage (
    day        TEXT    NOT NULL,  -- YYYY-MM-DD, UTC
    provider   TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    requests   INTEGER NOT NULL DEFAULT 0 CHECK (requests >= 0),
    tokens_in  INTEGER NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out INTEGER NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    PRIMARY KEY (day, provider, model)
) STRICT, WITHOUT ROWID;

CREATE TABLE audits (
    id               INTEGER PRIMARY KEY,
    match_id         INTEGER NOT NULL REFERENCES matches (id) ON DELETE CASCADE,
    auditor_provider TEXT    NOT NULL,
    auditor_model    TEXT    NOT NULL,
    auditor_score    REAL    NOT NULL CHECK (auditor_score BETWEEN 0 AND 10),
    delta            REAL    NOT NULL,
    verdict          TEXT    NOT NULL
                     CHECK (verdict IN ('justified', 'overrated', 'underrated', 'unclear')),
    notes            TEXT,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE INDEX audits_match ON audits (match_id);
CREATE INDEX audits_created ON audits (created_at);

CREATE TABLE kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;
