# Evaluation report

How well JobHunt picks and scores jobs, measured on 2026-10-19 (brief §8). Two
kinds of evidence:

1. **The golden set** (`eval/golden/`): 170 real postings from the live sources,
   10 synthetic profiles and 320 labelled (profile, job) pairs, run through the
   production code by `jobhunt eval`. CI replays it on every change.
2. **A manual review** of 44 scored pairs from a live collection: every pair
   scoring 8 or more, the rest of the top 32, and samples from the middle and
   the bottom.

All labels are **silver**: drafted during development, not yet confirmed by
the owner. `jobhunt eval label` turns them gold, and the report then shows gold
and silver separately. Treat the numbers below as the developer's measurement,
not an independent one.

## Method

`jobhunt eval` stores each golden job in an in-memory database and runs the
real stages on it: normalization, gates, title classification and fact
extraction (from recorded model answers), gates again, then the scorer for every
profile against every job. Nothing is re-implemented, except the **hard-gate
oracle**: it applies the product's hard rules (CSE role, job location, the
user's location choice, tracks, seniority) to the hand-checked labels instead of
the extracted facts. So a wrong extraction that lets a job through shows up as a
violation.

Recorded answers are keyed by a hash of the prompt and the posting. A changed
prompt or posting misses its recording and falls back to rules-only facts, and
the "jobs (by an LLM)" row shows how many jobs had a model answer.

The live answers were recorded by the Audit workflow's "Golden set on live
models" job, using Groq's free tier (`openai/gpt-oss-120b`, then
`qwen/qwen3.8-27b`, then `openai/gpt-oss-20b`). No Gemini key was set.

## Golden-set results

GOLDEN_RESULTS

Thresholds in `config/scoring.yaml` (`eval`) sit a little under these values. CI
fails on any regression past them, and on any hard-gate violation.

### Rules-only baseline

The same set, with no model answers at all, so every job gets rules-only facts
(what happens on a day when every free model is out of quota):

| Metric | Rules only |
| --- | ---: |
| hard-gate violations | 0 |
| accuracy: is_cse_role | 0.79 |
| accuracy: primary_domain | 0.64 |
| accuracy: min_years | 0.81 |
| accuracy: remote_eligibility | 0.80 |
| skills F1 | 0.82 |
| Spearman rho (score vs label) | 0.62 |
| precision@5 | 1.00 |
| false-positive rate at threshold | 0.00 |

Rules-only facts never decide a gate (docs/DECISIONS.md), so a job that needs
the description stays held back until a model reads it. The baseline is safe,
but it misses jobs: 26 eligible jobs are held back without a model.

## Defects found, and the fixes

Each fix has a regression test. The first group came out of the golden set
(Phase 11a, listed in docs/DECISIONS.md). These came from the live recordings
and the manual review:

1. **A region-locked job reached a user.** We Work Remotely tags some jobs
   "Anywhere in the World" next to a list of countries that leaves out
   Bangladesh (Lemon.io). The gate correctly asked the model, but the model
   only repeated the tag as "Location: Anywhere in the World, ...". The check
   for "the model just echoed the doubted tag" compared the whole quote,
   ellipsis included, so it missed it. Each piece of an ellipsis-joined quote is
   now checked on its own, and the job is held back
   (`location:worldwide_unconfirmed`).
2. **A track leak.** The model filed "Postgres Deployment Engineer (Nix)"
   (packaging, CI/CD, deployments at a database company) under
   software-engineering, so a backend profile got a DevOps job. In
   `extraction.v2` the primary domain is defined as the role's daily work, not
   the employer's industry, and the prompt shows the board's tags ("DevOps &
   Infrastructure").
3. **IT roles rejected as non-computing.** The title classifier answered "none"
   for Head of IT, IT Assistant, CTO, Manager - MIS and Engineering Manager,
   because the prompt said ambiguous titles are "none". These are common
   Bangladeshi computing jobs. `titles.v2` names IT, MIS and ERP roles and
   technology leads as computing roles.
4. **Paraphrased skills.** The model wrote skills as phrases ("AWS EC2 and S3",
   "writing queries"). The grounder dropped them correctly, but they were lost.
   The prompt now asks for one name per entry, spelled as in the posting.
5. **One listed skill gave full skill marks.** Live review pair 1: Supabase's
   "Postgres Engineer" lists one skill the rules recognise (PostgreSQL), so a
   data engineer who knows PostgreSQL got 3/3 for skills and 8.8 overall for a
   senior role. A posting that names fewer than 3 skills now earns at most 2.25
   for skills, and the alert says "only 1 listed" (`few_listed` in
   `scoring.yaml`, scorer.v2). That pair is now 8.1, and the two Bangladeshi IT
   support jobs that asked a fresh graduate for 2 years dropped from 8.5 to 7.8.
6. **The eligibility metric measured an unused answer.** `remote_eligibility`
   accuracy scored the model's answer even when the listing decides the
   location by itself (a source that lists only worldwide jobs, a Bangladeshi
   city, a named country), and in those cases the gates never read it. It now
   scores the value the gates act on. On the same answers it was 0.68 before
   and 0.90 after: the model's answer differed from the label mostly on
   "Remote" postings, where it is unused.

## Manual review of live pairs

On 2026-10-19 a local run collected the live sources (1,249 listings) and
gated them. With no model key on the development machine, facts came from the
rules: 112 jobs passed every job gate, and 153 more waited for a model. The 10
golden profiles were scored against those 112 jobs, which gave 140 pairs passing
the user gates. Two scored 8 or more (four before fix 5).

My label: **2** apply, **1** maybe, **0** not relevant. "Sent" means the score
reaches the profile's `min_score`.

| # | Score | Profile | Job | Sent | Label | Note |
| ---: | ---: | --- | --- | :---: | :---: | --- |
| 1 | 8.1 | data_engineer_mid | Postgres Engineer, Supabase (remote) | ✓ | 1 | Senior database engineering, not data pipelines. Was 8.8 before fix 5; still generous, since rules found no years ("Senior" is only in the text). |
| 2 | 8.0 | mobile_junior | Flutter Developer (Android & iOS), Regnum, BD | ✓ | 2 | Exact track, 2-5 years against 2, five matching skills. The README example. |
| 3 | 7.8 | network_it_fresh | Junior Executive - IT Support, RMG group, Gazipur | ✓ | 1 | Right work (AD, CCNA). Asks 2 years of a fresher; the 0.5/2 experience shows it. |
| 4 | 7.8 | network_it_fresh | IT Support & Network Specialist, Kazi Resort, Gazipur | ✓ | 1 | As 3: right work, asks 2-3 years. |
| 5 | 7.8 | backend_senior_remote | Desarrollador de Software Semi senior (Node.js), Ready | ✓ | 0 | **Wrong.** Spanish-language LATAM freelance contract. See "Open issues". |
| 6 | 7.7 | backend_senior_remote | Full-Stack TypeScript Developer, Plug & Solve (BD, remote) | ✓ | 1 | TypeScript fits; asks 3 years of a senior, probably junior pay. |
| 7 | 7.6 | backend_senior_remote | Laravel Developer, Mynztrip (BD, work from home) | ✓ | 1 | In track, but a 3-year Laravel job for a Go/Python senior. Rules found no seniority, so no mismatch penalty. |
| 8 | 7.6 | backend_senior_remote | Software Engineer, AI Coding Tasks, Askable (WWR) | ✓ | 1 | A contract writing coding tasks for AI training. Fair, but not a backend role. |
| 9 | 7.6 | backend_senior_remote | Staff Software Engineer (SRE), Phantom | ✓ | 2 | Senior infrastructure software, remote anywhere. |
| 10 | 7.5 | web_intern | Software Development (Intern), Software Lighthouse, BD | ✓ | 2 | An internship in the track. |
| 11 | 7.5 | backend_senior_remote | Software Engineer, AI Coding Tasks, Askable (Himalayas) | dup | 1 | The same job as 8 from another board. Same fingerprint, so it is sent once. |
| 12 | 7.4 | backend_senior_remote | Full Stack Developer, Sefbee Tech (BD, remote) | ✓ | 1 | Asks 2 years; few of the profile's skills. |
| 13 | 7.3 | ai_fresh_grad | Internship - Artificial Intelligence, BDTASK, BD | ✓ | 2 | Exactly the profile. |
| 14 | 7.3 | devops_mid_remote | Platform Engineer, Managed Postgres, Supabase | ✓ | 2 | Cloud platform work, remote. |
| 15 | 7.3 | backend_senior_remote | Software Engineer - Auth, Supabase | ✓ | 2 | Backend, 4+ years, Go/TypeScript. |
| 16 | 7.3 | backend_senior_remote | Senior Node.js Developer, Enjoy Gaming | ✓ | 2 | Senior backend, remote. |
| 17 | 7.3 | data_engineer_mid | Senior Data Engineer, Clera | ✓ | 2 | In track; senior, but no years stated. |
| 18 | 7.2 | backend_senior_remote | Software Engineer, micro1 | ✓ | 1 | A vague posting from a contractor marketplace. |
| 19 | 7.1 | backend_senior_remote | Senior Software Engineer, Infrastructure Platform, Lattice | ✓ | 2 | |
| 20 | 7.1 | backend_senior_remote | Software Engineer, Backend, Vercel | ✓ | 2 | |
| 21 | 7.1 | backend_senior_remote | Senior Web Engineer, Deed | ✓ | 2 | 6 years against 6. |
| 22 | 7.0 | ai_fresh_grad | AI Platform Engineer, Supabase | ✓ | 1 | AI tooling, but the posting expects experience that rules couldn't read. |
| 23 | 7.0 | devops_mid_remote | Platform Engineer, Managed Postgres, Supabase (Ashby) | dup | 2 | The same job as 14. |
| 24 | 7.0 | network_it_fresh | Officer-IT, garments manufacturer, BD | ✓ | 1 | Right work; asks 2 years. |
| 25 | 7.0 | backend_senior_remote | Software engineer, Sticker Mule | ✓ | 2 | |
| 26 | 7.0 | backend_senior_remote | Senior Backend Engineer, Consumer Growth, Sword Health | ✓ | 2 | |
| 27 | 6.9 | devops_mid_remote | DevOps Engineer (Linux), inovex | ✓ | 1 | A German consultancy; the posting is likely Germany-focused, yet Himalayas lists it with no restriction. |
| 28 | 6.9 | devops_mid_remote | Junior DevOps Engineer, Affidea | ✓ | 1 | Junior for a mid-level user. Fine to see, not a strong fit. |
| 29 | 6.7 | web_intern | Full Stack Developer, F.A. Creative Firm, BD | ✓ | 1 | Asks 1 year of an intern. |
| 30 | 6.7 | backend_senior_remote | Sr. Shopify Engineer, Utility | ✗ | 1 | Below the profile's 7.0: right. |
| 31 | 6.6 | data_analyst_fresh | Data Scientist, Ready | ✓ | 1 | In track; the Spanish-language company from 5. |
| 32 | 6.6 | devops_mid_remote | FinOps Data Engineer, Supabase | ✓ | 1 | Cloud cost data, adjacent. |
| 33 | 6.0 | qa_junior | Manual QA Engineer, FundCount | ✓ | 2 | In track, 2 years against 1.5. |
| 34 | 5.8 | ai_fresh_grad | Data Scientist, Ready | ✗ | 0 | Adjacent, not sent: right. |
| 35 | 5.8 | devops_mid_remote | Senior DevOps Engineer, Documo | ✗ | 1 | Not sent because rules found none of the user's skills. A model would read more. |
| 36 | 5.8 | devops_mid_remote | Site Reliability Engineer, Supabase | ✗ | 1 | Asks 7 years of 4: not sent, reasonable. |
| 37 | 5.8 | mobile_junior | Web Developer - PHP Laravel, Khan Revive, BD | ✗ | 0 | Adjacent web role, not sent: right. |
| 38 | 5.8 | backend_senior_remote | Staff Software Engineer, Revenue Systems, Squarespace | ✗ | 2 | **Missed.** Rules matched none of the user's skills; a model's skills would raise it. |
| 39 | 3.5 | security_junior | Network Engineer (NOC), AMC Digital, BD | ✗ | 0 | Adjacent, asks 4 years: right. |
| 40 | 3.5 | security_junior | Sr. Hardware and Network Engineer, Sinha-Medlar, BD | ✗ | 0 | Right. |
| 41 | 3.5 | qa_junior | Odoo Developer, Majesto, BD | ✗ | 0 | Right. |
| 42 | 3.5 | qa_junior | Software Engineer (PHP & ReactJS), BYSL, BD | ✗ | 0 | Right. |
| 43 | 3.0 | qa_junior | Senior Software Engineer, F.A. Creative Firm, BD | ✗ | 0 | Right. |
| 44 | 2.8 | qa_junior | Software Engineer - Auth, Supabase | ✗ | 0 | Right. |

Summary of the review:

- Of the 30 sent pairs (duplicates counted once), 14 are 2 (apply), 15 are 1
  (maybe) and 1 is 0 (the Spanish job). No sent pair breaks a hard rule: every
  one is a computing role in the profile's tracks, in Bangladesh or open to
  Bangladesh, within the seniority gap.
- Of the 12 pairs not sent, 10 are right to be held back. Two are misses (35
  and 38): on rules-only facts the skills part is thin. These jobs would be
  read by a model in production.
- Scores track my labels: every sent 2 scores 6.0-8.0, and every 0 except
  pair 5 sits under its profile's threshold.
- Scores are not inflated. Two of 140 pairs (1.4%) reach 8, far below the
  brief's 25% inflation warning. The main weakness is the other way: on
  rules-only facts, "maybe" jobs (1s) cluster at 6.6-7.8, close to the "apply"
  jobs.

## Open issues

- **Posting language.** A posting written in Spanish or German still scores
  normally (pair 5). The model extracts `language_requirements`, which the alert
  shows as a warning, but rules-only facts don't, and no gate uses it. A
  possible fix: a user setting for languages, with a hard gate on postings whose
  required languages the user doesn't have.
- **Coverage depends on model quota.** Groq's free tier covers about 50-60
  extractions per model per day. A Gemini key (SETUP section 9) is what lets
  every job be read by a model.
- **Silver labels.** The owner should confirm the labels (`jobhunt eval label`,
  then `--pairs`) so the gold columns fill in.
- **Rules-only seniority.** Without a model, the seniority mismatch penalty
  rarely applies, because rules can't read the seniority from the text (pair 7).
