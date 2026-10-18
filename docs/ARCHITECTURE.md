# Architecture

JobHunt finds computing jobs for CSE students and graduates in Bangladesh and
sends each user the ones that fit them, with a score they can trace to rules and
quotes. Everything runs on free tiers.

## Components

```
                  ┌────────────────────── Cloudflare (free) ──────────────────────┐
 Telegram users ─▶│ Worker "jobhunt-bot" (TypeScript, grammY, webhook)            │
  /start, buttons,│  • onboarding, /settings, alert buttons, /why, CV intake,     │
  CV upload       │    /admin, /broadcast, /export, /delete                       │
                  │  • cron */10 → repository_dispatch run-pipeline               │
                  │  • cron  :05 → heartbeat check → admin alert                  │
                  │ D1 (SQLite): the only database                                │
                  └───────────────▲───────────────────────────────────────────────┘
                                  │ D1 REST API (parameterized, batched)
 ┌──────────────── GitHub Actions (public repository: free runners) ─────────────┐
 │ pipeline.yml: run-pipeline | backfill-user | parse-cv | send-digest | broadcast │
 │   collect → normalize → store → gates → enrich (LLM facts) → gates → match      │
 │   → outbox → send (Telegram) → daily maintenance → runs row + heartbeat         │
 │ deploy.yml: D1 migrations → wrangler deploy (+ secrets) → setWebhook           │
 │ audit.yml:  weekly auditor + golden set on live models                          │
 │ ci.yml:     lint, types, tests, golden-set evaluation, repository rules         │
 └────────────────────────────────────────────────────────────────────────────────┘
 LLMs (OpenAI-compatible, free tiers): Gemini, then Groq; budgets in llm_usage.
```

**Why this split.** The bot must answer instantly at any hour; a Worker has no
cold start and the free plan allows 100,000 requests a day. The pipeline makes
hundreds of outbound requests and parses large pages, which the Worker's free
limits (10 ms CPU, 50 subrequests per invocation) don't allow, so it runs on
GitHub Actions, unlimited for public repositories. Both sides use one D1
database, so a user who signs up in the bot is visible to the next pipeline run
(the v1 bot's per-machine database was why friends never got alerts).

## One pipeline run

1. **Collect** (`jobhunt.sources`): every source that is due (per-source
   `min_interval_minutes`, state in `source_state`) is fetched concurrently,
   with per-host rate limits, conditional GETs and retries. A failing source
   never stops the others; three failures in a row, or a sudden run of empty
   results, alert the admins.
2. **Normalize and store** (`jobhunt.normalize`, `jobs/store.py`): text
   cleanup, canonical URL, cross-source fingerprint
   `sha1(company | title key | seniority | city)`; new rows are detected with
   `INSERT ... DO NOTHING RETURNING`. Unchanged listings are not rewritten.
3. **Gates** (`jobhunt.gates`): CSE (taxonomy match, exclusions, tech signals),
   location (Bangladesh, or remote for anyone; strict by default), freshness
   (10 days, deadline). Each answers pass, fail (with a reason) or needs_facts.
4. **Enrich** (`jobs/enrich.py`): unknown tech titles are classified in
   batches; up to 60 open jobs a run get facts from the LLM (strict JSON
   schema, grounded against the posting: every quote must be in the text);
   the gates run again on the facts. If no model has room, rules-only facts
   are stored and the job waits for the next run.
5. **Match** (`score/matching.py`): new or re-gated jobs against every user,
   and users whose settings changed against every open job. Hard gates per
   user (tracks, location choice, job type, seniority, scam markers), then the
   deterministic rubric (`config/scoring.yaml`): role 3, skills 3, experience
   2, education 1, preference 1. The "Why this score?" text is rendered and
   stored with the match.
6. **Outbox and send** (`notify/`): one notification row per new match at or
   above the user's minimum, unique per (user, job fingerprint). Rows are
   claimed with a lease, freshness is checked again, quiet hours and the
   per-run cap are honoured, Telegram limits are respected, a 403 marks the
   user blocked. Digest users get theirs once a day.
7. **Maintenance** (first run of each local day): expire stale jobs, clear old
   descriptions. Then the run is recorded in `runs` and, if every stage
   succeeded, the heartbeat in `kv` is updated.

## Data model

All tables are STRICT SQLite, created by `migrations/` (applied by wrangler to
D1 and by `jobhunt.db.apply_migrations` to local SQLite).

| Table | Holds |
| --- | --- |
| `users`, `user_prefs`, `user_profiles` | the Telegram identity, settings (tracks, level, locations, job types, minimum score, delivery mode, quiet hours) and skills/education |
| `jobs`, `job_domains`, `job_facts`, `title_classes` | listings, their taxonomy domains, LLM facts with grounding failures, cached title classes |
| `matches` | one row per (user, job) that passed every hard gate: score, breakdown, explanation, gate results, scorer version, rendered "why" |
| `notifications` | the outbox: status, attempts, Telegram message id, lease |
| `feedback` | applied, saved, hidden, 👍, 👎 with reason |
| `source_state`, `runs`, `llm_usage`, `kv` | source scheduling and health, run history, LLM quota, heartbeat and once-a-day markers |
| `audits` | the auditor's re-scores |
| `rate_limits` | the bot's per-user update window |

Deleting a user cascades to everything that is theirs.

## Hot queries and their indexes

| Query (where) | Index |
| --- | --- |
| Open jobs for enrichment, newest first (`enrich._OPEN_JOBS`) | `jobs_status_first_seen` |
| Existing rows for a batch of listings (`store._existing`) | `UNIQUE (source, source_job_id)` |
| Jobs not yet scored (`load_jobs(unscored_only=True)`) | partial `jobs_to_score` |
| Domains of the jobs being scored | `job_domains` primary key `(job_id, domain_slug)` |
| Matches of new jobs / changed users (`_existing_pairs`) | `matches_job`, `UNIQUE (user_id, job_id)` |
| Pending notifications, oldest first (`sender._PENDING`) | partial `notifications_pending` |
| A user's notification for a fingerprint (outbox insert) | `UNIQUE (user_id, fingerprint)` |
| Users by status (`/admin`, matching) | `users_status` |
| Today's LLM use (`LlmBudget.load`) | `llm_usage` primary key `(day, provider, model)` |
| Last run, heartbeat (`/admin`, cron) | `runs_started`, `kv` primary key |

D1's free tier counts every row read and written, including index entries;
the estimate behind these choices is in docs/DECISIONS.md ("Staying inside
D1's free tier").

## Configuration

Code holds logic only. Data and tunables are in `config/*.yaml` (validated by
pydantic at startup; the Worker gets a generated, zod-checked copy through
`jobhunt build-shared`), prompts in `prompts/`, words in `locales/en.json`,
secrets in the environment. `scripts/check_hardcoding.py` keeps URLs, model
ids, chat ids, cron strings and scoring numbers out of the code.

## Proving the scores

Facts are grounded in quotes from the posting; the score is computed by code
from facts, profile and `scoring.yaml`, never by a model; every component is
shown with its rule and quote. The golden set (`eval/golden/`) measures
extraction and scoring in CI on recorded model answers, and a weekly auditor
model re-scores real matches without seeing the system's score
(docs/RUNBOOK.md, "Evaluation"; docs/EVAL_REPORT.md).
