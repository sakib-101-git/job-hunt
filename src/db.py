import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from src.models import ScrapedJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    source_job_id       TEXT NOT NULL,
    title               TEXT NOT NULL,
    company             TEXT NOT NULL,
    location            TEXT,
    posted_date         TEXT,
    url                 TEXT NOT NULL,
    jd_text             TEXT,
    salary_range        TEXT,
    job_type            TEXT,
    fit_score           INTEGER,
    fit_reason          TEXT,
    cv_path             TEXT,
    cover_letter_path   TEXT,
    status              TEXT DEFAULT 'new',
    seen_at             TEXT NOT NULL,
    notified_at         TEXT,
    applied_at          TEXT,
    UNIQUE(source, source_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def job_exists(db_path: str, source: str, source_job_id: str) -> bool:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE source=? AND source_job_id=?",
        (source, source_job_id),
    ).fetchone()
    conn.close()
    return row is not None


def insert_job(db_path: str, job: ScrapedJob) -> int:
    conn = _connect(db_path)
    posted = job.posted_date.isoformat() if job.posted_date else None
    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs
           (source, source_job_id, title, company, location, posted_date,
            url, jd_text, salary_range, job_type, seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job.source, job.source_job_id, job.title, job.company,
            job.location, posted, job.url, job.jd_text,
            job.salary_range, job.job_type,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def update_score(db_path: str, job_id: int, score: int, reason: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE jobs SET fit_score=?, fit_reason=? WHERE id=?",
        (score, reason, job_id),
    )
    conn.commit()
    conn.close()


def update_cv_paths(db_path: str, job_id: int, cv_path: str, cover_letter_path: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE jobs SET cv_path=?, cover_letter_path=? WHERE id=?",
        (cv_path, cover_letter_path, job_id),
    )
    conn.commit()
    conn.close()


def update_status(db_path: str, job_id: int, status: str) -> None:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    if status == "notified":
        conn.execute("UPDATE jobs SET status=?, notified_at=? WHERE id=?", (status, now, job_id))
    elif status == "applied":
        conn.execute("UPDATE jobs SET status=?, applied_at=? WHERE id=?", (status, now, job_id))
    else:
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


def get_new_jobs(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM jobs WHERE status='new'").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(db_path: str) -> dict:
    conn = _connect(db_path)
    by_status = {
        row["status"]: row["cnt"]
        for row in conn.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status").fetchall()
    }
    by_source = {
        row["source"]: row["cnt"]
        for row in conn.execute("SELECT source, COUNT(*) AS cnt FROM jobs GROUP BY source").fetchall()
    }
    conn.close()
    return {"by_status": by_status, "by_source": by_source}


if __name__ == "__main__":
    from src.config import load_config
    config = load_config()
    init_db(config.db_path)
    print(f"Database initialised at {config.db_path}")
