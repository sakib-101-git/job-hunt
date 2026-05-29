# Job Hunt Automation

Automated job scraping, scoring, CV tailoring, and notification pipeline.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in your credentials in .env
```

## Configuration

- `config.yaml` — search keywords, locations, job sources
- `profile.yaml` — your master CV / profile data

## Usage

```bash
python src/main.py            # run full pipeline
python src/main.py --scrape   # scrape only
python src/main.py --tailor <job_id>  # tailor CV for a specific job
```

## Output

Generated PDFs land in `output/`. The SQLite database is at `data/jobs.db`.
