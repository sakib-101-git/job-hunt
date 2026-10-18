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
