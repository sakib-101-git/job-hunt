# Setup

How to get a development machine ready for JobHunt. This is a draft: sections for
Cloudflare, Telegram, D1 and the GitHub secrets are added as those parts land.

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
