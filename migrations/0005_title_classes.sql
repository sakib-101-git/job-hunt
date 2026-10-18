-- LLM title classification cache (brief §6.3): one answer per normalized title
-- and prompt version, so a title costs one LLM call ever. domain_slug NULL
-- means "not a computing role".
CREATE TABLE title_classes (
    title          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    domain_slug    TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (title, prompt_version)
) STRICT, WITHOUT ROWID;
