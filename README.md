# JobHunt

A Telegram bot that finds computing jobs for CSE students and graduates in
Bangladesh, and sends each person only the ones that fit them. A job counts if
it is in Bangladesh, or remote and open to anyone, anywhere. Each alert comes
with a score you can trace to a written rule and a quote from the posting.
Everything runs on free tiers.

## What an alert looks like

This one was rendered by the production code from a real BDJobs posting in the
golden set, with the facts a free LLM extracted from it, for a synthetic profile
(a Flutter developer with two years' experience):

```
🎯 Flutter Developer (Android & iOS)
🏢 Regnum · 📍 Anywhere in Bangladesh (On-site)
📅 Posted 4 days ago · ⏳ Apply by 13 Nov

⭐ 7.9/10 · Good match for you
✅ Role: Mobile, Desktop & Cross-Platform Applications, in your 📱 Mobile & Desktop Apps track (3/3)
➖ Skills: Flutter, Dart, REST API, Git, Kotlin ✓ · missing: Provider, Riverpod, Bloc, GetX, SQLite, Hive +16 more · 27 listed, 10 count (1.35/3)
✅ Experience: asks 2–5 yrs; you're a junior (1-3 yrs) (2/2)
✅ Education: CSE ✓ (1/1)
➖ Preferences: On-site ✓ · salary shown (0.5/1)

💬 Develop and maintain high-quality Flutter mobile applications for Android and iOS platforms.
🔗 via BDJobs

[🔗 Apply]  [❓ Why this score?]
[✅ Applied] [💾 Save] [🙈 Hide]
[👍] [👎]
```

**❓ Why this score?** shows, for each part of the score:
- the rule that gave the points;
- the user's value against the job's value;
- the exact sentence from the posting.

It also lists the checks the job passed and the scorer version.

## How it works

- **Collect.** Every 10 minutes a pipeline on GitHub Actions collects jobs from:
  - BDJobs (IT category);
  - company career boards (Greenhouse, Lever, Ashby, Workable, SmartRecruiters);
  - We Work Remotely, Remote OK, Jobicy, Working Nomads, Himalayas;
  - Hacker News "Who is hiring";
  - optionally, the owner's LinkedIn alert emails.
- **Classify.** Each job is matched against a taxonomy of more than 2,000 computing job titles, then checked:
  - CSE roles only;
  - Bangladesh, or remote for anyone (strict by default);
  - at most 10 days old.
- **Extract facts.** A free LLM (Gemini, then Groq) extracts the facts once per job. Every claim must quote the posting, or it is dropped.
- **Score.** Code scores each job for each user from those facts, their settings and `config/scoring.yaml`. A model never decides a score.
- **Send.** Alerts go out through the Telegram bot: instantly, or once a day as a digest, with quiet hours and no duplicates.
- **Bot and data.** The bot runs on a Cloudflare Worker (always on, no cold starts). Everything lives in one Cloudflare D1 database.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the full picture.

## Why the scores can be trusted

- **Grounded facts:** quotes are checked against the posting.
- **Deterministic scoring:** a versioned rubric; the same job and profile always give the same score.
- **A golden set:** 170 real postings, 10 synthetic profiles and 320 labelled pairs. CI measures every change against it.
- **A weekly audit:** an independent model re-scores a sample of real matches without seeing the system's score.

The method and results are in [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).

## Use it

Open the bot in Telegram and send `/start`. Setup takes about a minute:
1. Career tracks (AI, security, web, data, mobile, networking and more).
2. Experience level.
3. Where you can work.
4. Job types.
5. Your skills: type them or send a CV.
6. How strict matching should be and how you want alerts delivered.

Your best current matches arrive within a few minutes. `/help` lists every command. `/privacy` says what is stored, and `/delete` removes all of it.

## Run your own

[docs/SETUP.md](docs/SETUP.md) walks through the free accounts (Telegram,
Cloudflare, Gemini, Groq, GitHub) and the first deploy, step by step.

For development on Linux:

```bash
make setup       # Python and Node dependencies, git hooks
make check       # lint, types, tests, golden-set evaluation, repository rules
make run-local   # one pipeline run on a local SQLite database and a test bot
make bot-dev     # the Worker locally, on a local D1
```

## Documentation

- [docs/SETUP.md](docs/SETUP.md): development setup, production setup, the owner's checklist
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data flow, data model
- [docs/RUNBOOK.md](docs/RUNBOOK.md): operating it: sources, alerts, evaluation, secrets
- [docs/DECISIONS.md](docs/DECISIONS.md): dated decisions and the facts behind them
- [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md): how well it works, measured
- [docs/PRIVACY.md](docs/PRIVACY.md): what is stored and who processes it
