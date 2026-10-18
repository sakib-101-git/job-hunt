-- The structured fields a source published but jobs has no column for (work
-- mode, employment type, salary, eligibility restrictions, tags, source extras),
-- so later stages can re-evaluate a job without fetching it again.
ALTER TABLE jobs ADD COLUMN listing_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(listing_json));
