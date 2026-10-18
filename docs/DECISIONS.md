# Decisions

Dated record of technical choices and the facts behind them. Newest entries go at
the top of each section. Re-check a fact before acting on it after its date.

## Toolchain

### 2026-10-18: Node 24, moving to Node 26 after 2026-10-28

Node 24 is Active LTS until 2026-10-20. Node 26 becomes LTS on 2026-10-28; bump
`.nvmrc`, `engines` and `@types/node` then.

### 2026-10-18: TypeScript pinned to 6.0.3

typescript-eslint 8.71.2 supports TypeScript below 6.1, and TypeScript 7.0 is too
new for the toolchain. Revisit when typescript-eslint supports a newer release.

### 2026-10-18: Worker tests use @cloudflare/vitest-plugin

`@cloudflare/vitest-pool-workers` is deprecated. Its replacement,
`@cloudflare/vitest-plugin` 1.4.4 (the `cloudflareTest()` plugin), works with
vitest 5.0.5.

### 2026-10-18: Install scripts allow-listed in package.json

npm 11.19 blocks dependency install scripts by default. Only esbuild and workerd,
which need theirs to fetch platform binaries, are approved in `allowScripts`.

### 2026-10-18: Worker `Env` type is generated

`wrangler types` writes `bot/worker-configuration.d.ts` (git-ignored). The
`pretypecheck` and `prelint` scripts regenerate it. Listing secret names under
`[secrets] required` in `wrangler.toml` keeps the output the same on every machine.

### 2026-10-18: nodejs_compat is not set explicitly

It is on by default for compatibility dates from 2026-08-04, and `wrangler.toml`
uses 2026-10-12.

## CI

### 2026-10-18: GitHub Action versions

checkout v7, setup-node v7, gitleaks-action v3. setup-uv no
longer publishes a floating major tag, so it is pinned to v10.3.0; bump it by hand.

### 2026-10-18: gitleaks-action without a licence key

gitleaks-action is free for repositories on personal accounts, so CI uses it with
only `GITHUB_TOKEN`.

### 2026-10-18: No AI attribution, enforced in three places

The `commit-msg` hook, CI (commit messages, PR title and body) and
`scripts/repo_guard.py tracked-files` (no AI-tool files such as `CLAUDE.md`) all
use the same rules in `scripts/repo_guard.py`.

## Config and taxonomy

### 2026-10-18: config/ layout and loading

`config/taxonomy/job_titles.yaml` and `tracks.yaml` are the owner's files, copied
unchanged (their SHA-256 sums match the originals). Matching rules live beside
them in `title_rules.yaml`; tunables in `app.yaml`; skills and Bangladesh places in
`skills.yaml` and `locations.yaml`. Every file is validated by a pydantic model
that rejects unknown keys, and `jobhunt validate-config` runs in CI. Cross-file
rules (track domains exist and are selectable; every `include_titles` entry is in
`job_titles.yaml` verbatim) are checked at load time.

The taxonomy marks the seniority ladder with `kind: seniority_ladder`; code reads
that field instead of a hard-coded slug.

### 2026-10-18: Python dependencies

pydantic 2.13.5 (2.14 was ten days old), PyYAML 6.0.3 with types-PyYAML,
RapidFuzz 3.14.6, Hypothesis 6.170.1 for property tests.

### 2026-10-18: Title matching design

Exact -> alias -> fuzzy, then (in the extraction phase) a batched LLM classifier
for what is left.

- Both sides go through one normalizer: segments that are only places, work-mode
  words or job codes are dropped; abbreviations expand; "engineer", "developer"
  and "programmer" fold together; seniority words at either end become a
  separate signal. The match key ignores word order but keeps repeats.
- Aliases are taxonomy titles without brackets or a dash qualifier.
- Fuzzy matching repairs typos with RapidFuzz, then scores IDF-weighted token
  overlap. Plain token-set ratio was rejected: it scores "Civil Engineer" close to
  "Software Engineer". Listing words that the candidate lacks cost full weight
  unless they belong to the candidate's domains. A candidate must share the
  listing's role noun (last word).
- A bracket-free or segment variant of a listing title is tried only when every
  word it drops is a taxonomy word, so "Production Engineer (Garments)" never
  becomes "Production Engineer".

### 2026-10-18: Title matching calibration

Measured on `pipeline/tests/data/title_cases.txt` (390 real-world titles, labels
written before tuning) and `non_cse_titles.txt` (64 non-CSE titles):

| min_similarity | correct | wrong domain | unmatched | non-CSE matched |
| --- | --- | --- | --- | --- |
| 0.65 | 96.4 % | 5 | 9 | 3 |
| **0.70** | **95.6 %** | **4** | **13** | **2** |
| 0.75 | 94.4 % | 3 | 19 | 2 |
| 0.80 | 93.8 % | 3 | 21 | 0 |

0.70 is used (re-measured after one-word titles stopped matching fuzzily; see
the gates section). The 4 wrong-domain cases land in an adjacent domain (for example
"Applied Scientist" in data science). The 2 non-CSE matches ("Industrial
Engineer", "IT Sales Executive") are on `non_cse_exclusions`, which the CSE gate
applies. Unmatched titles use words the taxonomy lacks ("CodeIgniter", "Cloud
Architect") and go to the LLM classifier. Re-run the table after any rule change.

### 2026-10-18: build-shared is a CLI command

The brief suggested `scripts/build_shared`. It is `jobhunt build-shared` instead,
so it reuses the config loader and its validation. It writes
`bot/src/generated/config.json` (domains, tracks, skills, preferred cities), and CI
fails if the committed file is stale.

## Database

### 2026-10-18: Cloudflare D1 facts (Workers Free plan)

Checked in the D1 docs and in wrangler 4.151.1's source:

- REST endpoint `POST /accounts/{account}/d1/database/{database}/query`, body
  `{sql, params}` or `{batch: [{sql, params}, ...]}`; one `sql` string may also
  hold several `;`-separated statements. Batched statements run as one SQL
  transaction.
- Limits: 500 MB per database, 5 GB per account, 5 million rows read and 100,000
  rows written per day (reset 00:00 UTC; queries fail until then), 50 queries per
  Worker invocation, 100 bound parameters per query, 100 KB per statement, 2 MB per
  row. The brief's "5 GB" is the account total, not one database.
- The Cloudflare API allows 1,200 requests per five minutes per user and answers
  429 with Retry-After. `D1HttpBackend` retries 429, 5xx and network errors with
  backoff and honours Retry-After.
- Foreign keys are always enforced and can't be turned off by queries (a
  migration may use `PRAGMA defer_foreign_keys = on`). The SQLite backend
  switches them on to match.
- `d1_migrations(id, name UNIQUE, applied_at)`; wrangler runs each file together
  with the INSERT that records it, from `migrations_dir` (files `NNNN_name.sql`).
  `jobhunt.db.apply_migrations` copies this exactly.
- `database_id` is optional in `wrangler.toml`, so the id stays out of the repo.

To verify against a live database in the automation phase: the REST docs type
`params` as strings, while the backend sends JSON numbers and nulls.

### 2026-10-18: Schema conventions

STRICT tables (column types enforced in both D1 and SQLite), ISO 8601 UTC text
timestamps, CHECK constraints on enums and `json_valid` on JSON columns.
Product defaults (minimum score, delivery mode, caps) are not schema defaults:
the application writes them from config.

Additions to the brief's data model:

- `notifications.fingerprint` with `UNIQUE (user_id, fingerprint)`, so a job seen
  on two boards reaches a user once, even across overlapping runs.
- `job_domains.is_primary` with a partial unique index: one primary per job.
- `jobs.remote_eligibility_evidence` for the location gate's quote.
- `user_prefs.include_adjacent` (the per-user adjacent-domains toggle) and
  `preferred_cities_json` (the soft city preference, apart from the location gate).
- Deleting a user cascades to prefs, profile, matches, notifications, feedback and
  audits, which is what `/delete` needs.

### 2026-10-18: One async database interface

`jobhunt.db.Database` has `query`, `execute`, `batch` (atomic) and
`execute_script`. `D1HttpBackend` (httpx 0.28.1) serves production;
`SqliteBackend` serves development and tests. `open_database` picks D1 when
`CLOUDFLARE_ACCOUNT_ID`, `D1_DATABASE_ID` and `CLOUDFLARE_API_TOKEN` are all set,
else `JOBHUNT_SQLITE_PATH`. Constraint violations raise `ConstraintError` on both.
The Worker uses the D1 binding directly through `bot/src/db/` with prepared
statements; its tests apply the same migrations with `applyD1Migrations`.

## Sources

### 2026-10-18: Fetching

httpx 0.28.1 with HTTP/2 (h2 4.4.1). One `Fetcher` per run: per-host concurrency
and minimum spacing between requests (overrides per host in `sources.yaml`),
retries on 429/5xx/network errors with jittered exponential backoff, Retry-After
honoured but capped, other 4xx fail at once. Feeds that return their whole list in
one response use conditional GETs; the validators live in `source_state.state_json`
per URL, since several sources poll more than one URL (migration 0002). Sources run
concurrently; one failing never stops the others.

### 2026-10-18: Source terms decide what is enabled

Checked each site's terms and robots.txt (details in docs/RUNBOOK.md):

- Enabled: BDJobs, Remote OK, We Work Remotely, Jobicy, Working Nomads, Himalayas.
  Their attribution and Apply-link rules are recorded in `sources.yaml` and will
  shape the alert message.
- Remotive is implemented but disabled: its terms forbid using its jobs to collect
  signups and ask for at most four requests a day. The owner can enable it.
- Himalayas forbids passing its jobs on to "other job boards, aggregators or job
  search engines". A bot that delivers jobs to its own users, with Himalayas
  credited and Apply on Himalayas' link, is the integration their clause 113
  allows ("personal and commercial integrations"), not a further job board.

### 2026-10-18: BDJobs by category, not by keyword

The search API's `category=8` (IT/Telecommunication) covers the whole IT market
(about 300 open jobs) in four pages, instead of hundreds of keyword searches.
`postedWithin` keeps incremental runs small; details are fetched only for jobs not
yet stored. A bootstrap run of ~300 detail requests at 1.5 s spacing takes about
eight minutes, inside the brief's ten-minute bootstrap target.

### 2026-10-18: Fixtures are recorded, not hand-written

`jobhunt fetch --record` saves real responses; tests replay them through the same
`Fetcher`, so parsing and paging are tested end to end without network access.
Fixtures are trimmed to a few items, and emails and phone numbers in job text are
replaced with `[email]` and `[phone]`.

### 2026-10-18: Company boards, HN and LinkedIn

- Company boards are one source (`company_boards`) over a verified company list,
  so adding a company is a config change. Job ids are `<ats>:<token>:<id>`.
  Workable's v3 list endpoint is POST-only; its public widget API
  (`/api/v1/widget/accounts/<token>?details=true`) gives the same jobs with
  descriptions in one GET. SmartRecruiters (not in the brief's list) was added
  because a Dhaka employer (Polygon Technology) uses it.
- Seven companies passed the check; most well-known remote employers did not,
  because their current roles are region-restricted.
- HN keep/skip patterns live in config. A post's title is never invented: with no
  role segment, the poster's own first line is the title.
- LinkedIn alerts are read over IMAP, read-only, and only when the owner's IMAP
  variables are set (`required_env`: such a source is "not configured", never
  "failed"). The parser was written against a synthetic email until a real,
  redacted one is available.

## Normalization and storage

### 2026-10-18: Listings to jobs

- Descriptions become plain text (`html_to_text`): block tags become line breaks,
  list items keep a "• " bullet, entities and double-escaped HTML are decoded,
  NFKC, spaces collapsed. Stored text is capped at `jd_max_chars_stored` at a word
  boundary; `jd_max_chars_llm` is the extraction phase's cap.
- Canonical URLs drop tracking parameters (`normalization.tracking_params`),
  lower-case scheme and host, drop the fragment and sort the remaining query.
- Fingerprint = sha1(company | title key | seniority | city). Company names drop
  configured suffixes ("Ltd", "Limited", ...); the title key comes from the same
  normalizer the taxonomy uses, so "Sr. Software Engineer" and "Senior Software
  Developer" agree, while seniority keeps a senior and a junior opening apart;
  the city is the Bangladesh district a location names ("Gulshan" and "Dhaka" are
  both `dhaka`), else the first part of the location, else "remote".
- Fields with no column (work mode, employment type, salary, restrictions, tags,
  source extras) go to `jobs.listing_json` (migration 0003), so later stages can
  re-process a job without fetching it again.
- Upserts keep `first_seen_at`, refresh `last_seen_at`, keep the earliest known
  posting date, and replace the description only with a longer one: a job seen
  again without its details request never loses its full text.

## Gates

### 2026-10-18: Three answers, not two

Each gate returns pass, fail or `needs_facts`. `needs_facts` means the listing
alone can't decide (a remote job with no stated eligibility, a tech title the
taxonomy doesn't know, no posting date); such a job stays open, goes to LLM
extraction, and the gate runs again on the extracted facts. Gates are pure
functions; `jobs/gating.py` stores every result with its reason and quote in
`jobs.gates_json` (migration 0004), the first failure as `filter_reason`, the
title match in `job_domains`, and the location quote in
`remote_eligibility_evidence`.

### 2026-10-18: CSE gate

- The owner's `non_cse_exclusions` match whole words on normalized titles.
  An entry with "(Non-X)" ("Sales Engineer (Non-Software)") is a "depends": a title
  without X goes to extraction instead of failing. Found on real data: "Ubuntu
  Sales Engineer" at Canonical is software sales.
- Unknown titles go to the LLM only with a tech word in the title
  (`cse_gate.tech_signals`). On 938 live jobs this cut LLM-bound titles from 509 to
  about 127 ("Video Creator", "Client Advisor" fail as `no_tech_signal`). Listing
  tags were dropped as a signal: Remote OK tags nearly everything "technical".
- One-word titles ("Analyst", "Head of Growth" once seniority is stripped) are
  matched exactly or not at all; fuzzy matching on one word gave wrong domains.

### 2026-10-18: Location gate

- Strict by default, each eligibility entry judged on its own: any entry that names
  a Bangladeshi place, or says worldwide/anywhere without naming another country or
  region, passes the job. "Home based - EMEA; Home based - Worldwide" passes;
  "REMOTE (Worldwide: US / Canada / Europe)" does not.
- Places come from data: Bangladesh from `locations.yaml`, everything else from
  `geography.yaml` (84 countries with spellings and cities, regions, worldwide
  phrases, remote words, time-zone markers, filler words). Aliases of up to three
  capitals ("US", "EU") match only in capitals, so "join us" is not the US.
- An unknown place on a non-Bangladesh source fails (`not_bangladesh`): it is
  neither Bangladesh nor worldwide. On BDJobs an unknown place passes unless a
  foreign place or overseas note shows up (defect D5).
- Time-zone requirements never reject; they are returned as warnings for the
  message.

### 2026-10-18: Freshness and seniority

- Freshness is judged in Asia/Dhaka: posted at most `max_age_days` (10) ago,
  exactly 10 days included; deadline not passed. With no posting date: only a
  future deadline, first seen within 10 days, outside a bootstrap run. LinkedIn
  alerts have no posting date; the alert's send time stands in for it.
- The seniority gate is per user and lives in config (`seniority_gate`): blocked
  title seniority per level, a maximum years gap, and default years per level.
- Jobs from a source's first run are stored with `seeded: true` and never notified.

### 2026-10-18: Measured

- BDJobs bootstrap (303 IT jobs with details): 459 s, within the 10-minute target.
- On 938 live jobs from the other sources: 70 passed every gate, 127 need
  extraction, the rest were filtered (320 no tech signal, 193 too old, 183
  region-restricted, 24 not in Bangladesh, 17 regions without Bangladesh, 4 non-CSE
  titles).

## Extraction

### 2026-10-18: Providers and models

Both providers serve OpenAI-compatible chat completions with strict JSON-schema
output (`response_format: json_schema, strict: true`):

- Gemini: `https://generativelanguage.googleapis.com/v1beta/openai`. Current
  models `gemini-3.8-flash` and `gemini-3.5-flash-lite`; reasoning can't be turned
  off on Gemini 3, so `reasoning_effort: low`. Free-tier limits are shown only in
  AI Studio for a key's project, so `config/llm.yaml` holds conservative
  placeholders to replace once a key exists. Free-tier inputs may be used to
  improve Google's products: job text only, never CVs.
- Groq: `https://api.groq.com/openai/v1`. `openai/gpt-oss-120b`,
  `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`: 30 requests/min, 8K tokens/min,
  1,000 requests and 200K tokens a day each. Strict mode needs every property
  required (optional ones nullable) and `additionalProperties: false`;
  `strict_schema` produces exactly that from the pydantic model.

Not yet verified live: no keys were available. Owner action: add
`GEMINI_API_KEY` and `GROQ_API_KEY`, then run `jobhunt enrich --limit 5` and
check the answers.

### 2026-10-18: Budget

`llm_usage` keeps daily requests and tokens per model across runs; per-minute
requests and tokens are tracked in memory for the run. A model is skipped
before a request would pass 95 % of a daily limit (`daily_safety_margin`), the
budget waits up to `max_wait_seconds` for per-minute room before moving on, and a
429 skips that model for the rest of the run. Groq's 8K tokens/minute allows
only two or three extractions a minute per model, which the budget spreads over
the chain.

### 2026-10-18: Facts are grounded

- `prompts/extraction.v1.md`: facts only, a verbatim quote next to every fact,
  temperature 0, the schema pinned to the taxonomy's domain slugs. A schema miss
  gets one retry with the validation error.
- The grounding validator resets any fact whose quote isn't in the posting
  (fuzzy match at least 90 after normalization), and also requires every number
  in a quote to be in the posting and `min_years` to be in its quote: a test
  showed fuzzy matching alone accepting "5 years" for "1 year". Skills must
  appear in the posting; known ones become skills.yaml ids.
- Years and deadline are cross-checked against a regex and the listing;
  disagreements go to `gates_json.audit_flags` for the auditor.
- Facts are cached in `job_facts` by an input hash (prompt version, title,
  company, location, description); a job is re-extracted only when that changes.

### 2026-10-18: Rules-only facts never decide a gate

When no model has room, `extract/rules.py` produces facts from regex and the
vocabulary (years only next to "experience", degrees, non-ambiguous skills,
structured fields). They are stored for scoring, but gates keep their listing
verdict and the job is extracted again by an LLM on a later run. Skills that are
ordinary words ("R", "Unity", "Excel") are marked `ambiguous` in skills.yaml and
skipped by the rules.

### 2026-10-18: Title classification

Tech titles the taxonomy doesn't know are classified in batches of 25 into a
domain slug or "none" (`prompts/title_classification.v1.md`), cached per
normalized title in `title_classes` (migration 0005). "none" filters the job
without spending an extraction call; a slug becomes a `job_domains` row with
method `llm`.

## Scoring

### 2026-10-18: The rubric is code and config, never the LLM

`config/scoring.yaml` holds every weight; `score/scorer.py` applies them to
grounded facts and the user's profile. Components are rounded to 2 decimals and
the total to 1 decimal with `Decimal` ROUND_HALF_UP, so the parts in a message
add up to the score shown. The config is checked at load time: weights sum to
10, each component's top value equals its weight, labels run down to 0.
`scorer_version` is stored on every match.

- Role: primary domain of a chosen track 3.0; a secondary domain or the track's
  `include_titles` 2.25 (the owner's example: the AI track gets Python and R
  developer roles); an adjacent domain 1.5 if the user keeps adjacent domains;
  otherwise a hard gate (a security-only user never gets a frontend job).
- Skills: (2 × must matched + nice matched) / (2 × must + nice) × 3; 1.5 when the
  posting lists none. Skills compare as skills.yaml ids.
- Experience: met 2.0; 1 year short 1.0; 2 years short 0.5; unknown 1.5; a job
  seniority that doesn't fit the user's level costs 0.5.
- Education: CSE/CS/IT/software or no degree required 1.0; any engineering or
  science 0.75; another specific field 0.
- Preferences (capped at 1): preferred city 0.5, work mode 0.3, salary shown 0.2.

Per-user hard gates: chosen tracks, the user's location choice (a worldwide
remote job needs "remote"; an on-site Bangladesh job needs "Bangladesh"; a remote
job open to Bangladesh suits either), job type, the seniority gate and scam
markers (`red_flags` in app.yaml). A pair failing a gate is counted in the run
summary, not stored.

Property tests (Hypothesis): adding a matching skill never lowers the score;
asking for more years never raises the experience points; the components always
add up to the score.

### 2026-10-18: Messages

`score/render.py` renders brief §7.3's alert and the "Why this score?" view as
Telegram HTML from `locales/en.json`: every word is in the locale, every number
comes from the stored breakdown, values are escaped (`& < >`) exactly once, and
each alert credits its source ("via Jobicy"), as the sources' terms require.
Jobs scored on rules-only facts say "Quick match: AI review pending". A test
checks the locale covers every label, enum value and rule the renderer can hit.

## Notifications

### 2026-10-18: Outbox, then send

`jobhunt match` stores matches and queues one `notifications` row per new match
at or above the user's minimum score; `jobhunt notify` sends what is queued. The
row is unique per (user, job fingerprint), so a job seen on two boards, or
matched by two overlapping runs, is sent once. Seeded jobs (a source's first
run) and users who are not active get no rows.

Sending re-checks freshness and the deadline, honours delivery mode (instant
users every run, digest users on the digest run or `/digest`) and quiet hours,
and sends at most `max_per_cycle` full alerts per user; the rest go out as one
list. Every row ends sent (with Telegram's message id), failed (with the error;
retried on later runs up to `max_attempts`) or skipped (with the reason), and
the run's counts say exactly that: a failed send is never counted as sent.

### 2026-10-18: Telegram limits

The Bot FAQ allows about 30 messages a second overall and one a second per chat;
the client stays under both (25/s, 1.1 s). A 429 waits Telegram's `retry_after`,
5xx and network errors back off, and a 403 marks the user blocked so they get
nothing more. Messages use HTML with link previews off. Inline buttons send
`<code>:<match id>` (codes in `config/app.yaml`, shared with the Worker through
build-shared), well under Telegram's 64-byte `callback_data` limit.
