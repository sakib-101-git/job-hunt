-- Every gate's decision for a job, with its reason and quote:
-- {"cse": {...}, "location": {...}, "freshness": {...}, "needs_facts": [...], "seeded": bool}.
-- Kept for audits and for the "Why this score?" view.
ALTER TABLE jobs ADD COLUMN gates_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(gates_json));
