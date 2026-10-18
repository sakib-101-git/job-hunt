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
