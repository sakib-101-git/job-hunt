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

### Shomvob
- Site may be JS-rendered. If requests+BS4 returns empty cards, switch to playwright
- Check `scraping.use_playwright: true` in config.yaml if needed

### Skill.jobs
- Similar JS rendering concern as Shomvob

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
