# Runbook

Operational notes for JobHunt. Sections are added as each part of the system lands.

## Sources

Every source is configured in `config/sources.yaml` and implemented in
`pipeline/src/jobhunt/sources/`. Each endpoint, the site's terms and its robots.txt
were checked on 2026-10-18; re-check before changing a source.

### Checking a source by hand

```bash
cd pipeline
uv run jobhunt sources                          # what is configured, and why some are off
uv run jobhunt fetch bdjobs jobicy --show 10    # live fetch, nothing stored
uv run jobhunt fetch jobicy --record /tmp/rec   # also save every response
```

To run the due sources and store their jobs in a local SQLite database (created
and migrated on first use):

```bash
JOBHUNT_SQLITE_PATH=/tmp/jobhunt.db uv run jobhunt collect            # due sources
JOBHUNT_SQLITE_PATH=/tmp/jobhunt.db uv run jobhunt collect --source bdjobs --force
```

It prints, per source, its status, how many listings it returned and how many were
new, plus any warnings and health alerts.

Tests never touch the network: they replay `pipeline/tests/fixtures/sources/<name>/`.
To refresh a fixture, record it as above, keep a handful of items, scrub emails and
phone numbers from job text, and copy the directory over.

### Adding a source

1. Check the endpoint, its terms (attribution, redistribution, rate limits) and
   robots.txt. Write down what you found here.
2. Add a settings model in `jobhunt/config/models.py` and an entry in
   `config/sources.yaml` with `min_interval_minutes` and `attribution`.
3. Implement `fetch(ctx) -> SourceResult` returning `RawListing`s. Never invent a
   location, date or "Worldwide": missing stays `None`.
4. Record a fixture and test the parser against it.

### Health alerts

A source that fails `failures_before_alert` runs in a row, or returns nothing for
`empty_runs_before_alert` runs after it used to return jobs, raises one alert (and
another when it recovers). A feed answering 304 Not Modified is not "empty".

### BDJobs (primary Bangladesh source)

- Search: `GET api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch` with `category=8`
  (IT/Telecommunication, about 300 open jobs), `pg`, `rpp` (up to 100), `isPro=0`
  and `postedWithin=<days>` on incremental runs. Other filters the web client knows:
  `industry`, `location`, `jobNature`, `jobLevel`, `deadline`, `keyword`, `workplace`.
- Details: `GET gateway.bdjobs.com/ActtivejobsTest/api/JobSubsystem/jobDetails?jobId=<id>&ln=1`
  gives the full description (`JobDescription`, `EducationRequirements`,
  `experience`, `SkillsRequired`, `JobKeyPoints`), `DeadlineDB`, `JobLocation`,
  `JobWorkPlace`, `JobNature`, `overseasnote` and `Closed`. Despite "Test" in the
  host name, it is what bdjobs.com itself calls. Details are fetched only for jobs
  not stored yet.
- Send `Origin`/`Referer: https://bdjobs.com` as the web client does.
- `Confidential` is a real company value. Keep it.
- BDJobs lists jobs abroad too; `overseas_note` and the location feed the
  location gate (defect D5).
- Terms (bdjobs.com/tos.asp) say nothing about automated access; bdjobs.com's
  robots.txt only blocks internal folders and api.bdjobs.com has none.
- If the endpoints change: download `https://bdjobs.com/h/`, follow its
  `chunk-*.js` files and search them for `searchEndpoint` and `detailsApiBaseUrl`.

### Remote boards

| Source | Endpoint | Eligibility field | Terms |
|---|---|---|---|
| Remote OK | `remoteok.com/api` (latest 100; first item is a legal notice) | `location`, often empty | Link back to the Remote OK job URL and name Remote OK. robots.txt: crawl-delay 1. |
| We Work Remotely | `weworkremotely.com/categories/<category>.rss` (7 tech feeds) | `<region>`, `<country>`, `<state>` | Public feeds; link to the WWR job page. |
| Jobicy | `jobicy.com/api/v2/remote-jobs?count=100&geo=anywhere` | `jobGeo` | Credit Jobicy with a direct link; Apply opens the job's own URL. |
| Working Nomads | `www.workingnomads.com/api/exposed_jobs/` (current list) | `location` | No published terms; robots.txt allows it. |
| Himalayas | `himalayas.app/jobs/api/search?country=BD&sort=recent&page=N` (20 a page) | `locationRestrictions` (empty means worldwide) | Clause 113: Apply uses `applicationLink`, visible credit, never pass jobs on to other job boards. Optional free key (`HIMALAYAS_API_KEY`): 60 requests/minute. |

### Disabled sources

- **Remotive:** its API terms forbid "displaying our jobs in order to collect
  signups", which a bot with onboarding does, ask for at most 4 requests a day,
  delay jobs by 24 hours, and robots.txt disallows `/api/*`. The source is
  implemented and tested; enabling it is the owner's decision.
- **skill.jobs:** `studio.skill.jobs/api/job_search/` answers from Bangladeshi
  residential IPs (checked 2026-10-18) but sits behind Cloudflare, which v1 saw
  answer 403 to GitHub Actions runners. Re-check from a runner before enabling; never
  try to get around Cloudflare.
- **Shomvob:** `shomvob.com/jobs` still returns 404 (2026-10-18); its job list comes
  from an obfuscated RPC and holds almost no software roles.
- **The Muse:** US-centric; worldwide-remote roles are rare.

### Company career boards (`company_boards`)

Public ATS APIs: Greenhouse, Lever, Ashby, Workable (its public widget API, which
includes descriptions) and SmartRecruiters (a details request per new job). The
companies are listed in `config/sources.yaml` with what was checked for each.

To add a company:

1. Find its board token (the part after the ATS host in its careers URL).
2. Check the API answers 200, e.g.
   `curl -s https://boards-api.greenhouse.io/v1/boards/<token>/jobs | head`.
3. Check it hires in Bangladesh or remote-worldwide: look for locations such as
   "Dhaka", "Bangladesh", "Worldwide", "Anywhere" or "Remote, Global" (not
   "Remote - US").
4. Add `{name, ats, token, verified}` with what you saw, and run
   `uv run jobhunt fetch company_boards`.

Checked 2026-10-18: Populate, Canonical, Supabase, Toptal and Polygon Technology
have matching roles; SELISE and Brain Station 23 (Dhaka) have valid Workable
boards with no open roles yet. Big remote employers such as GitLab, Stripe and
Datadog were left out: none of their current roles is worldwide. One company's
board failing is a warning in the run report; all of them failing fails the source.

### Hacker News "Who is hiring?" (`hn_whoishiring`)

Algolia HN API: the newest thread by `whoishiring` (`search_by_date`), then its
comments (`search?tags=comment,story_<id>&hitsPerPage=1000`, about 400 a month).
Only top-level comments matching `require` (REMOTE) and one of `require_any`
(worldwide, anywhere, global) are kept; the location gate still checks each one.
Posts are free text, so titles are best effort: segments matching `not_a_title`
(links, contract types, pay) are skipped, and when no role is left the poster's
first line is used as is.

### LinkedIn (owner's alert emails only)

- Read-only over IMAP with a Gmail App Password (`IMAP_HOST`, `IMAP_EMAIL`,
  `IMAP_APP_PASSWORD`); the source shows as "not configured" until all three are
  set. It searches `FROM "jobs-noreply@linkedin.com" SINCE <lookback>`.
- Each job comes from a `/jobs/view/<id>` link: the link text is the title and the
  card's next two text lines are company and location. Alerts carry no posting
  date; the email's send time is kept as `alert_sent_at`.
- **The parser is built against a synthetic email**
  (`pipeline/tests/fixtures/emails/linkedin_alert_synthetic.eml`). Replace it with a
  real alert email, personal details redacted, and adjust `parse_alert` if needed.
- linkedin.com pages are never fetched.

## Config: adding titles, tracks, skills and places

All of these are YAML edits; no code changes. After any edit run `make config`
(validates everything) and `make build-shared` if the bot's view changed, then
`make test`.

- **A job title:** add it to `additions` in `config/taxonomy/job_titles.yaml` under
  its domain. Keep directory titles as they are.
- **A track, or a domain in a track:** edit `config/taxonomy/tracks.yaml`. Domains
  must exist and must not be the seniority ladder; `include_titles` entries must be
  copied from `job_titles.yaml` exactly.
- **An abbreviation, spelling or seniority word:** `config/taxonomy/title_rules.yaml`.
  Quote words that YAML reads as booleans (`on`, `no`, `yes`).
- **A skill or alias:** `config/skills.yaml`. Names and aliases must be unique
  across all skills (case-insensitive). Never change an existing id.
- **A place spelling or neighbourhood:** `aliases` or `areas` in
  `config/locations.yaml`.

### When a title is matched wrongly

1. Reproduce it:

   ```bash
   cd pipeline
   uv run python -c "from jobhunt.config import load_config
   from jobhunt.taxonomy.matcher import TitleMatcher
   print(TitleMatcher(load_config()).match('Jr. SQA Engr.'))"
   ```

2. Add the title with its correct domains to `pipeline/tests/data/title_cases.txt`
   (or to `non_cse_titles.txt`).
3. Prefer a data fix (a title addition or a rule in `title_rules.yaml`) over changing
   `title_matching` in `config/app.yaml`. If you change thresholds, redo the
   calibration table in `docs/DECISIONS.md`.

## Gates: why a job was filtered

`jobs.filter_reason` names the first gate that failed (`location:region_restricted`),
and `jobs.gates_json` holds every gate's status, reason and the text it was decided
on. To look:

```bash
sqlite3 /tmp/jobhunt.db "SELECT title, filter_reason, json_extract(gates_json, '$.location')
                          FROM jobs WHERE filter_reason LIKE 'location:%' LIMIT 20"
```

Tuning is data, not code:

- A place spelled a new way: `config/locations.yaml` (Bangladesh) or
  `config/geography.yaml` (elsewhere). Add a test row to
  `pipeline/tests/data/location_cases.yaml`.
- A tech title the CSE gate rejects as `no_tech_signal`: add the word to
  `cse_gate.tech_signals` in `config/app.yaml`, or the title to the taxonomy.
- Policies: `location_policy` in `config/app.yaml` (accept unspecified remote jobs,
  accept "APAC"-style regions); `freshness.max_age_days`; `seniority_gate`.

## LLM extraction

`jobhunt enrich` classifies unknown titles, extracts facts from open jobs and
runs the gates again. It prints which models have a key, and how many jobs went
to each model or to the rules-only fallback.

- **Keys:** `GEMINI_API_KEY` (aistudio.google.com) and `GROQ_API_KEY`
  (console.groq.com). Without either, every job gets rules-only facts and stays
  undecided until a model is available.
- **Limits:** after creating the Gemini key, open the AI Studio rate-limit page
  and copy the free-tier numbers into `config/llm.yaml` (`chain`). Usage so far
  today: `SELECT * FROM llm_usage WHERE day = date('now')`.
- **Changing the prompt:** copy it to a new version (`extraction.v2.md`), point
  `config/llm.yaml` at it and bump `prompt_version`; every job is then extracted
  again as runs come by. Never edit a prompt version that has been used.
- **A wrong fact:** look at `job_facts.grounding_failures_json` and
  `jobs.gates_json -> audit_flags` for the job, then fix the prompt (new version),
  not the code.

## Scores

- **Why did a user get this score?** `matches.explanation_json` has each
  component's points, rule, the user's and the job's values and the posting's
  quote; `matches.gate_results_json` has the per-user gates. The bot's "Why this
  score?" button shows the same.
- **Changing weights:** edit `config/scoring.yaml`, bump `scorer_version`, run
  `make test` (the config is checked: weights sum to 10), and re-check the golden
  set (evaluation phase) before merging. Feedback never changes weights by itself.
- **Wording:** every word of a message is in `locales/en.json`; a test fails if
  a label, rule or enum value the renderer uses is missing.

## Notifications

- **Run by hand:** `uv run jobhunt match` scores and queues;
  `TELEGRAM_BOT_TOKEN=... uv run jobhunt notify` sends instant alerts and any
  digest that is due, `--digest` every digest user's queue now, `--user ID` one
  user's queue whatever their mode.
- **Digests** go out once a day per digest user: the first run after
  `notifications.digest_time` (local time) sends everything waiting, unless the
  user already got something since that time (for example through `/digest`).
- **New users** get only their best `notifications.backfill_top_n` current
  matches (seeded jobs included), whichever run scores them first; their other
  current matches are stored and never sent.
- **A user says they got nothing:** check `users.status` (a 403 sets `blocked`),
  `user_prefs.min_score`, `delivery_mode` and `quiet_hours_json`, then their rows
  in `notifications`: `last_error` says why a row failed or was skipped.
- **Failed rows** are retried by later runs until `attempts` reaches
  `notifications.max_attempts` in `config/app.yaml`. To resend one, set its
  `status` back to `pending` and `attempts` to 0.

## Bot

- **A user is stuck in setup:** `users.onboarding_state` holds their draft;
  `/start` shows the current step again. Setting it to NULL restarts setup.
- **"Too many requests":** `bot.updates_per_minute` in `config/app.yaml`; the
  window is in `rate_limits`.
- **Changing options or words:** option lists and defaults are in
  `config/app.yaml` (`bot`), words in `locales/en.json`. Run
  `make build-shared` (or `uv run jobhunt build-shared`) so the Worker gets
  the change; CI fails when the generated file is stale.
- **Admins:** `ADMIN_TELEGRAM_IDS` (Worker secret, comma-separated ids) may use
  `/admin` (users, queue, today's LLM requests, last run, source health) and
  `/broadcast <text>` (preview, then the pipeline sends it). Others get the
  unknown-command reply.
- **Pipeline events the bot sends:** `backfill-user`, `parse-cv`, `send-digest`
  and `broadcast` (`repository_dispatch`, payload `user_id`, or `html` and
  `admin_id`); its cron sends `run-pipeline`. See [Automation](#automation).
- **Errors** are logged as JSON lines (`update failed`, with the update id,
  never the message text): `npx wrangler tail` in `bot/`.

## Automation

### What runs when

| Trigger | What happens |
| --- | --- |
| Worker cron `*/10 * * * *` | `repository_dispatch run-pipeline` → `pipeline.yml` runs `jobhunt event`: collect, enrich, match, notify, then the daily maintenance |
| `pipeline.yml` schedule `7,37 * * * *` | the same run, as a fallback if the Worker's trigger fails |
| Worker cron `5 * * * *` | heartbeat check (below) |
| A user finishes setup | `backfill-user`: scores open jobs for them (no LLM calls) and sends their best matches now |
| A user agrees to a CV read | `parse-cv`: downloads the PDF, reads it with Groq only, saves skills, years and education, messages a summary |
| `/digest` | `send-digest`: the user's waiting matches, now |
| `/broadcast` (admin) | `broadcast`: one message to every active user, then a count to the admin |
| Merge to `main` touching `bot/`, `migrations/`, `config/` or `locales/` | `deploy.yml`: D1 migrations, Worker deploy with its secrets, webhook and command menu |

The crons are in `bot/wrangler.toml` (`[triggers]`, and `[vars]` saying which
is which). Each run is a row in `runs` (trigger, status, per-stage counts and
seconds in `stats_json`) and a summary on the Actions run page. A stage that
fails is recorded and the later stages still run; the run is then `failed`.

### Switching things off

- Pause the pipeline: `gh variable set PIPELINE_ENABLED --body false` (runs
  are then skipped, not failed). The Worker keeps asking; nothing happens.
- Pause deploys: `gh variable set DEPLOY_ENABLED --body false`.
- Run once by hand: `gh workflow run pipeline.yml` (30-minute limit instead
  of 10, enough for a source's first run).

### Admin messages

| Message | Meaning | What to do |
| --- | --- | --- |
| ⚠️ Source alert: … is failing | a source failed `failures_before_alert` runs in a row | `uv run jobhunt fetch <source>` locally; see the source's section above |
| ⚠️ Source alert: … returned nothing | silent breakage: empty for `empty_runs_before_alert` runs after returning jobs | the site may have changed its API or markup |
| ⚠️ Source alert: … is working again | recovered | nothing |
| 🚨 The pipeline has stalled | no successful run for `heartbeat.max_age_minutes` | Actions → Pipeline: look at the last runs; check secrets and `PIPELINE_ENABLED`; `gh workflow run pipeline.yml` |
| 🚨 The pipeline has not completed a run yet | no run has ever succeeded | finish SETUP.md section 12 |
| ✅ The pipeline is running again | a run succeeded after a stall | nothing |
| 📣 Broadcast sent to N users | after `/broadcast` | users who blocked the bot are marked blocked |

The stall alert is sent once per stall (`heartbeat.alert_kv_key` in `kv` marks
it open) and once on recovery.

### Housekeeping

The first run of each local day expires open jobs that are no longer fresh
(deadline passed, or older than `freshness.max_age_days`) and clears
descriptions of jobs not seen for `retention.jd_text_days`, keeping their facts.
`kv.last_maintenance_day` records the day it last ran; delete that row to run
it again.

### Rotating secrets

Change the GitHub secret (`gh secret set NAME`), then run `gh workflow run
deploy.yml`: it uploads the Worker's secrets again and re-registers the webhook
(so a new `TELEGRAM_WEBHOOK_SECRET` takes effect at once). The pipeline reads
its secrets at every run. For a new bot token, also revoke the old one in
@BotFather (`/revoke`).

## Evaluation

The golden set in `eval/golden/` measures the whole system against
hand-checked labels (brief §8.3):

- `jobs.jsonl`: 170 real postings collected on 2026-10-18 from every enabled
  source, spread over sources and gate outcomes (60% passed the job gates,
  Bangladesh boards weighted double), with e-mail addresses and phone numbers
  redacted. Labels: `is_cse_role`, `primary_domain` (+ `secondary_domains`),
  `min_years`, `remote_eligibility`, `country`, `deadline`, `skills` and
  `eligible` (should reach anyone at all).
- `profiles.jsonl`: 10 synthetic users across the tracks. Never real users.
- `pairs.jsonl`: 320 (profile, job) pairs labelled 0 not relevant, 1 maybe,
  2 apply.
- `llm_answers.jsonl`: recorded model answers, so CI replays the evaluation
  without keys. A changed prompt or posting misses its recording and falls back
  to rules-only facts, which shows in the metrics.

Commands (from `pipeline/`):

- `uv run jobhunt eval` prints the report; `--check` fails below the thresholds
  in `config/scoring.yaml` (`eval`), as CI does; `make eval` runs that.
- `uv run jobhunt eval --live --record` calls the models and rewrites the
  recordings (needs `GEMINI_API_KEY` and/or `GROQ_API_KEY`).
- `uv run jobhunt eval label` walks the silver job labels: Enter accepts,
  `field=value` corrects (for example `min_years=2`, `skills=python, django`),
  `s` skips, `q` quits; each answer is saved at once and makes the label gold.
  `--pairs` does the same for pairs (Enter, `0`, `1` or `2`).
- `uv run jobhunt eval draft --db FILE --count N` drafts a new jobs file from a
  database the pipeline collected into (move the old one away first). Every
  drafted label then needs checking by hand.

### Refreshing the recordings

The weekly Audit workflow (or `gh workflow run audit.yml -f audit=false`) runs
the live evaluation and uploads `golden-set-live` (the answers and the report).
Download it with `gh run download <run id> -n golden-set-live`, copy
`llm_answers.jsonl` into `eval/golden/`, run `make eval`, and open a PR. Raise a
threshold in `scoring.yaml` when a metric has clearly improved; never lower one
to let a change pass.

### Labelling conventions

- `is_cse_role` follows the owner's taxonomy: engineering, data, IT support and
  administration, security, QA, technical support and UI/UX roles are CSE;
  product managers, sales, marketing, recruiters, translators and designers of
  graphics are not.
- `primary_domain` is a taxonomy slug; when a title appears in the Bangladesh
  additions of `job_titles.yaml`, its domain there wins. Other true domains go
  in `secondary_domains`; any of them counts as correct.
- `min_years` is the lowest number of years asked for ("3 to 5 years" is 3,
  "freshers may apply" is 0); ages and company histories never count.
- `remote_eligibility` uses the extraction prompt's definitions. A board's
  "Anywhere" tag counts as worldwide unless the posting itself limits who may
  apply (work authorization, a country list without Bangladesh, a time-zone
  band that is an explicit condition).
- `country` is where the job is based, null for fully remote jobs.
- `eligible`: CSE, and either in Bangladesh or remote for anyone (or naming
  Bangladesh).
- Pair labels were drafted by a written rule over the job labels (in-track,
  location, seniority, skills) and checked by hand; the owner's
  `jobhunt eval label --pairs` makes them gold.

### The weekly audit

`jobhunt audit` (Audit workflow, Sundays 03:13 UTC) samples every match scoring
8 or more from the last 7 days plus 10% of the rest (at most 40), and has the
auditor model (default `groq/openai/gpt-oss-120b`; `AUDITOR_PROVIDER`,
`AUDITOR_MODEL`, `AUDITOR_API_KEY` override it) score each one from the
posting and the user's settings alone, without seeing the system's score.
Within `audit.agree_within` points is "justified"; otherwise "overrated" or
"underrated". Results go to `audits`, the run summary and a short admin
message: agreement, mean difference, the worst disagreements, the score
histogram with inflation/narrow-band warnings, and 👍/👎 by track. Feedback
never changes weights by itself.

## Database migrations

Migrations live in `migrations/` and are shared: wrangler applies them to D1 and
`jobhunt.db.apply_migrations` applies them to local SQLite in tests.

- **Add one:** create the next file, `migrations/0002_<what>.sql` (numbers have no
  gaps). Never edit a file that has been applied anywhere.
- **Keep it backward compatible.** The pipeline and the Worker deploy separately, so
  a migration must work with the code already running: add first (expand), switch
  the code over, and drop old columns or tables in a later migration (contract).
- **Test it:** `make test` applies every migration to fresh SQLite and to the
  Worker's test D1. `make migrate-local` applies them to the local D1 used by
  `make bot-dev`.
- If a migration must violate foreign keys temporarily, start it with
  `PRAGMA defer_foreign_keys = on;`.
