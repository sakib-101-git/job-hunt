# Runbook

Operational notes for JobHunt. Sections are added as each part of the system lands.

## Sources: quirks and history

These notes come from v1 (October 2026). Every endpoint is re-verified before the
v2 source is implemented, and this section is updated with what was found. The v1
implementation can still be read with `git show 426e331:src/scrapers/<name>.py`.

### BDJobs (primary Bangladesh source)

- The old HTML search (`jobs.bdjobs.com/jobsearch.asp`) is dead: it 302s to an empty
  Angular shell.
- v1 used the JSON API `GET https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch`
  with `keyword`, `pg` (1-based page), `rpp` (results per page) and `isPro=0`.
  The response has `data[]`, `premiumData[]` and `common`.
- v1 sent `Accept: application/json, text/plain, */*`, `Origin: https://bdjobs.com`
  and `Referer: https://bdjobs.com/`, as the web client does.
- The JD comes straight from the listing item (`jobContext`, `eduRec`, `experience`),
  so no detail fetch is needed.
- Fields used: `publishDate`, `location`, `WorkPlace` (`Office`, `Home` or empty;
  `Home` means remote). The deadline field name still has to be identified.
- `Confidential` is a real company value (also the fallback when `companyName` is
  empty). Keep it as-is.
- BDJobs lists jobs abroad too (for example, Gulf countries). v1 let every BDJobs job
  bypass the location checks, so overseas jobs leaked through. v2 must detect them.
- If the endpoint or parameters change, re-inspect the Angular bundle: fetch
  `https://bdjobs.com/h/main-*.js` and find the env chunk's `searchEndpoint`.

### skill.jobs (disabled)

- It has a clean JSON API (`studio.skill.jobs/api/job_search/`) behind Cloudflare.
  Cloudflare returns 403 to datacenter IPs such as GitHub Actions runners, while
  residential Bangladesh IPs work. Header changes do not get past IP/TLS filtering,
  and bypassing it is out of scope. BDJobs covers the same employers.

### Shomvob (disabled)

- `shomvob.com/jobs` returns 404. The site is a Next.js app whose job list comes from
  an obfuscated Supabase RPC; `backend-api.shomvob.co` only exposes
  `get-top-companies`.
- It mostly lists blue-collar jobs and yields almost no software roles.

### LinkedIn (owner's alert emails only)

- Read over IMAP with a Gmail App Password, never the account password. v1 searched
  `FROM "jobs-noreply@linkedin.com" SINCE <date>` and took job IDs from
  `/jobs/view/<id>` links.
- LinkedIn changes the email HTML from time to time. Keep a redacted real email as a
  test fixture and update the parser against it.
- linkedin.com pages are never scraped.

### Remote boards used by v1

| Source | v1 endpoint | v1 defect to fix |
|---|---|---|
| Remotive | `https://remotive.com/api/remote-jobs?search=<kw>&limit=20` | missing location defaulted to "Worldwide" |
| RemoteOK | `https://remoteok.com/api?tags=<tag>` | missing location defaulted to "Worldwide" |
| We Work Remotely | `https://weworkremotely.com/categories/remote-*-programming-jobs.rss` | hard-coded "Worldwide"; ignored `<region>` |
| Jobicy | `https://jobicy.com/api/v2/remote-jobs?count=50&tag=<tag>` | |
| Working Nomads | `https://www.workingnomads.com/api/exposed_jobs/` | |
| The Muse | public API | US-centric; disabled by default in v2 |
