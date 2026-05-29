import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    external_id TEXT,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    url         TEXT,
    description TEXT,
    posted_at   TEXT,
    scraped_at  TEXT DEFAULT (datetime('now')),
    score       INTEGER,
    status      TEXT DEFAULT 'new',   -- new | applied | rejected | ignored
    cv_path     TEXT,
    cl_path     TEXT,
    UNIQUE(source, external_id)
);
"""


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_job(job: dict) -> int | None:
    sql = """
        INSERT INTO jobs (source, external_id, title, company, location, url, description, posted_at)
        VALUES (:source, :external_id, :title, :company, :location, :url, :description, :posted_at)
        ON CONFLICT(source, external_id) DO NOTHING
    """
    with connect() as conn:
        cur = conn.execute(sql, job)
        return cur.lastrowid if cur.rowcount else None


def get_unscored_jobs() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE score IS NULL").fetchall()


def update_score(job_id: int, score: int):
    with connect() as conn:
        conn.execute("UPDATE jobs SET score = ? WHERE id = ?", (score, job_id))


def get_job(job_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def set_job_paths(job_id: int, cv_path: str, cl_path: str):
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET cv_path = ?, cl_path = ?, status = 'ready' WHERE id = ?",
            (cv_path, cl_path, job_id),
        )
