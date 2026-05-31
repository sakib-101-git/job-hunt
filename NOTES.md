# JobHunt — Notes

## Scraper Quirks

### BDJobs
- Site migrated off `jobs.bdjobs.com/jobsearch.asp` (now a dead 302 → empty Angular
  shell). Now uses the JSON API: `GET https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch`
  with params `keyword`, `pg`, `rpp`, `isPro=0`. Response: `data[]` + `premiumData[]` + `common`.
- JD comes straight from the listing item (`jobContext`/`eduRec`/`experience`) — no detail fetch needed.
- "Confidential" is a valid company name, keep as-is (used as the fallback when companyName is empty).
- `WorkPlace` is "Office" / "Home" / "" — "Home" means remote.
- BD sources bypass the remote-only and age gates in `hard_filter` (local jobs are top
  priority, and BDJobs lists posts until their deadline, not just for 72h).
- If the API endpoint/params change again, re-inspect the Angular bundle: pull
  `https://bdjobs.com/h/main-*.js`, find the env chunk's `searchEndpoint`.

### Skill.jobs (removed)
- Had a clean JSON API (`studio.skill.jobs/api/job_search/`), but it's behind
  Cloudflare which 403-blocks datacenter IPs (GitHub Actions runners) while
  serving residential/BD IPs fine. Header tweaks don't bypass Cloudflare IP/TLS
  filtering. Removed from the project since BDJobs covers the same BD employers
  on the cloud. Would need a residential proxy to run skill.jobs from CI.

### Shomvob (currently disabled)
- Removed from ALL_SCRAPERS. Old `shomvob.com/jobs` 404s; the site is a Next.js app
  whose job list comes from an obfuscated Supabase RPC (table/RPC name not recoverable
  from the bundle; `backend-api.shomvob.co` only exposes `get-top-companies`).
- Also a blue-collar-focused platform, so it yields ~0 of the software roles we target.
  `src/scrapers/shomvob.py` is kept (old HTML-scraper code) but unused. Revisit only if
  shomvob exposes a usable jobs endpoint.

### LinkedIn Email
- Requires Gmail App Password (not your actual password)
- LinkedIn occasionally changes email HTML structure — check fixtures when broken

## Prompt Tuning Log

| Date | Change | Reason |
|------|--------|--------|
| -    | Initial prompts | baseline |

## JD Edge Cases

- Very short JDs (< 50 chars): assigned score=5, reason="JD too short to evaluate"
- No location in JD: passes location filter by default if config is lenient
- "Confidential" company in BDJobs: passes all filters, show in Telegram as-is
