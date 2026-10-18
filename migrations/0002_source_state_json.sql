-- Several sources poll more than one URL (feeds, pages), so per-URL HTTP
-- validators and cursors live in one JSON column instead of single
-- etag/last_modified columns. Nothing has been deployed yet, so the old columns
-- are dropped here rather than in a later contract step.
--
-- last_nonempty_at lets the health check tell "a source that normally returns
-- jobs went quiet" from "a source that is always empty".

ALTER TABLE source_state RENAME COLUMN cursor TO state_json;
ALTER TABLE source_state DROP COLUMN etag;
ALTER TABLE source_state DROP COLUMN last_modified;
ALTER TABLE source_state ADD COLUMN last_nonempty_at TEXT;
