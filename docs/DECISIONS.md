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
| **0.70** | **95.9 %** | **4** | **12** | **2** |
| 0.75 | 94.4 % | 3 | 19 | 2 |
| 0.80 | 93.8 % | 3 | 21 | 0 |

0.70 is used. The 4 wrong-domain cases land in an adjacent domain (for example
"Applied Scientist" in data science). The 2 non-CSE matches ("Industrial
Engineer", "IT Sales Executive") are on `non_cse_exclusions`, which the CSE gate
applies. Unmatched titles use words the taxonomy lacks ("CodeIgniter", "Cloud
Architect") and go to the LLM classifier. Re-run the table after any rule change.

### 2026-10-18: build-shared is a CLI command

The brief suggested `scripts/build_shared`. It is `jobhunt build-shared` instead,
so it reuses the config loader and its validation. It writes
`bot/src/generated/config.json` (domains, tracks, skills, preferred cities), and CI
fails if the committed file is stale.
