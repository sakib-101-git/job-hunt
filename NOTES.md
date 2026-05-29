# JobHunt — Notes

## Scraper Quirks

### BDJobs
- Search URL params need inspection on each run — site occasionally changes param names
- "Confidential" is a valid company name, keep as-is
- Premium listings have slightly different card HTML
- Pagination: first 2–3 pages only to avoid rate limits

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
