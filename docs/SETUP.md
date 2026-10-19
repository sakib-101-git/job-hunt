# Setup

How to get a development machine ready for JobHunt (sections 1-6), how to set
up the free accounts that run it in production (sections 7-12), and what only
the owner can do (section 13). Everything runs on free plans with no card.

## Requirements

| Tool   | Version                                | Used for                          |
| ------ | -------------------------------------- | --------------------------------- |
| git    | any recent                             | version control                   |
| make   | GNU make                               | `make check` and friends          |
| uv     | 0.13 or newer                          | Python 3.12 and pipeline packages |
| Node   | 24 (from `.nvmrc`)                     | the Worker in `bot/`              |
| Python | 3.12 (from `pipeline/.python-version`) | the pipeline; installed by uv     |

## 1. System packages

Ubuntu / Debian:

```bash
sudo apt update
sudo apt install -y git make curl unzip
```

Fedora:

```bash
sudo dnf install -y git make curl unzip
```

## 2. uv and Python 3.12

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
```

uv reads `pipeline/.python-version`, so `uv sync` also fetches 3.12 if it is missing.

## 3. Node 24 with fnm

```bash
curl -fsSL https://fnm.vercel.app/install | bash
# restart the shell, then from the repository root:
fnm install    # reads .nvmrc
fnm use
```

Any version manager that reads `.nvmrc` (nvm, volta, mise) works too.

## 4. Clone and install

```bash
git clone git@github.com:sakib-101-git/job-hunt.git
cd job-hunt
make setup     # uv sync, npm ci, and git hooks
cp .env.example .env
```

`make setup` points `core.hooksPath` at `.githooks/`, whose `commit-msg` hook
rejects AI attribution in commit messages.

## 5. Local database

```bash
make migrate-local   # local D1 for `make bot-dev`
```

The pipeline uses the SQLite file in `JOBHUNT_SQLITE_PATH` (from `.env`) when the
Cloudflare values are empty.

## 6. Check everything works

```bash
make check     # lint, typecheck, tests and repository rules
make help      # all targets
```

CI also runs gitleaks over the full history. To run it locally, install
[gitleaks](https://github.com/gitleaks/gitleaks/releases) and run `gitleaks git .`.

To try a full pipeline run on your machine against a local SQLite database
(`data/jobhunt.db`) and a test bot, put a test bot's token in `.env` and run:

```bash
make run-local
```

`run-local` ignores any Cloudflare values in `.env`, so it never touches the
production database.

## 7. Telegram bot

1. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot` and pick
   a name and a username ending in `bot`. Copy the token: it is
   `TELEGRAM_BOT_TOKEN`.
2. Make a webhook secret (1-256 characters of `A-Z a-z 0-9 _ -`):

   ```bash
   openssl rand -hex 32    # TELEGRAM_WEBHOOK_SECRET
   ```

3. Find your numeric Telegram user id (for example by messaging
   [@userinfobot](https://t.me/userinfobot)). Admin ids go in
   `ADMIN_TELEGRAM_IDS`, comma-separated.

The command menu and the webhook are set by the deploy workflow; nothing else
needs doing in BotFather.

## 8. Cloudflare (Worker and D1)

1. Create a free account at <https://dash.cloudflare.com/sign-up>. Note the
   **Account ID** (Workers & Pages → Overview, right side): `CLOUDFLARE_ACCOUNT_ID`.
2. Create the database:

   ```bash
   cd bot
   npx wrangler login
   npx wrangler d1 create jobhunt     # prints database_id: D1_DATABASE_ID
   ```

3. Create an API token (My Profile → API Tokens → Create Token → Custom token)
   with exactly these permissions, for your account only:
   - Account → **D1** → Edit
   - Account → **Workers Scripts** → Edit
   - Account → **Account Settings** → Read (wrangler reads the account)
   - User → **Memberships** → Read (wrangler reads the account)

   This is `CLOUDFLARE_API_TOKEN`. The pipeline uses it for the D1 HTTP API and
   the deploy workflow for wrangler.
4. Your Worker's address is `https://jobhunt-bot.<your-subdomain>.workers.dev`
   (Workers & Pages → your subdomain is shown on the overview). This is the
   `WORKER_URL` variable below.

## 9. LLM keys

- **Groq** (required for CV reading; also the fallback for job facts): sign up at
  <https://console.groq.com>, then API Keys → Create. `GROQ_API_KEY`.
- **Gemini** (first choice for job facts, and strongly recommended: Groq's free
  tier covers only about 50-60 job extractions per model per day):
  <https://aistudio.google.com/apikey> → Create API key. `GEMINI_API_KEY`. Check the key's real free limits at
  <https://aistudio.google.com/rate-limit> and update `config/llm.yaml` if they
  differ.

Without any key the pipeline still runs on rules-only facts ("⚡ quick match").

## 10. GitHub token for the Worker

The Worker starts pipeline runs with `repository_dispatch`. Create a
fine-grained token at <https://github.com/settings/personal-access-tokens/new>:

- Resource owner: `sakib-101-git`; Repository access: **Only select
  repositories** → `job-hunt`
- Repository permissions: **Contents: Read and write** (what the dispatches API
  requires); everything else stays "No access"
- Expiration: up to one year; put a reminder in your calendar to rotate it

GitHub secret names can't start with `GITHUB_`, so this token is stored as the
repository secret `GH_DISPATCH_TOKEN`; the deploy workflow hands it to the Worker
as `GITHUB_DISPATCH_TOKEN`.

If the "Generate token" button does nothing, the form usually has a field
that isn't filled in: a repository must be picked under "Only select
repositories", Contents must be set to "Read and write", and the name must not
match an existing token. GitHub also asks for your password or 2FA code just
before it generates the token, and a blocked pop-up or an ad blocker can hide
that prompt. If it still fails, a classic token works too
(<https://github.com/settings/tokens/new>, scope **repo**, with an expiry), but
it can reach all your repositories, so prefer the fine-grained one.

The token is optional. Without it the bot works and deploys, but it can't
start pipeline runs. The pipeline then runs on its 30-minute schedule instead
of every 10 minutes, new users get their first matches at the next run, and
reading CVs and `/broadcast` are unavailable (the bot says so). Add the secret
later and run the deploy workflow again.

## 11. GitHub secrets and variables

Repository → Settings → Secrets and variables → Actions. With the GitHub CLI:

```bash
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_WEBHOOK_SECRET
gh secret set ADMIN_TELEGRAM_IDS
gh secret set CLOUDFLARE_ACCOUNT_ID
gh secret set CLOUDFLARE_API_TOKEN
gh secret set D1_DATABASE_ID
gh secret set GH_DISPATCH_TOKEN      # recommended; see section 10 if it won't generate
gh secret set GROQ_API_KEY
gh secret set GEMINI_API_KEY
# optional
gh secret set HIMALAYAS_API_KEY
gh secret set IMAP_HOST; gh secret set IMAP_EMAIL; gh secret set IMAP_APP_PASSWORD

gh variable set WORKER_URL --body "https://jobhunt-bot.<your-subdomain>.workers.dev"
```

Each `gh secret set` asks for the value, so it never lands in your shell history.
The old v1 secret `TELEGRAM_CHAT_ID` is no longer used and can be deleted:
`gh secret delete TELEGRAM_CHAT_ID`.

## 12. First deploy and switching on

```bash
gh variable set DEPLOY_ENABLED --body true
gh workflow run deploy.yml          # migrations, Worker + secrets, webhook
gh run watch                        # wait for it to finish
gh variable set PIPELINE_ENABLED --body true
gh workflow run pipeline.yml        # first run: seeds every source, sends nothing
```

From then on the Worker asks for a run every 10 minutes, and every merge to
`main` that touches the bot, migrations, config or locales deploys again. Check:

1. Send `/start` to your bot and finish setup. You get "Finding your matches…",
   then your best current matches within a few minutes.
2. Send `/admin`: the last run, source health, LLM use and the queue.
3. Within the hour, nothing else should arrive; if the pipeline stalls for more
   than an hour, admins get a 🚨 message.

To invite friends, share `https://t.me/<your bot username>`; each of them runs
`/start` and gets alerts for their own tracks.

## 13. Owner checklist

One-time steps that only the owner can do, in order:

- [ ] Sections 7-11: create the bot, the Cloudflare database and token, the LLM
      keys and the GitHub token, and set the repository secrets and variables.
      A Gemini key matters: without it, Groq's free tier covers only about
      50-60 job extractions per model per day.
- [ ] Section 12: run the first deploy and the first pipeline run; finish
      `/start` yourself and check `/admin`.
- [ ] End-to-end test with at least two friends: each picks different tracks,
      and each should get alerts only for theirs (`/why` shows why).
- [ ] Delete the obsolete v1 secret: `gh secret delete TELEGRAM_CHAT_ID`.
- [ ] Ask GitHub Support to garbage-collect the pre-rewrite commits and refresh
      the contributors cache (Support portal → "Removing sensitive data"), if
      any old commit is still reachable by SHA.
- [ ] Delete the old v1 workflow runs (Actions → filter "JobHunt" → delete),
      which the coding agent was not allowed to do.
- [ ] Optional: forward one real LinkedIn job-alert email, with personal data
      removed, so the parser can be checked against a real one; then set the
      `IMAP_*` secrets.
- [ ] Optional: decide on Remotive (its terms ask for at most four requests a
      day and forbid using its jobs to collect signups; it is off).
- [ ] Turn silver labels gold: `uv run jobhunt eval label` and
      `uv run jobhunt eval label --pairs` (stop and resume any time).
- [ ] After 2026-10-28: move to Node 26 LTS (`.nvmrc`, `engines`, `@types/node`).

### Inviting friends

Share `https://t.me/<your bot username>`. For a QR code, open the bot's profile
in Telegram, tap the share icon and choose "QR Code"; print it or post it in a
group. Each person runs `/start` and sets up their own tracks; nobody sees
anyone else's data.
