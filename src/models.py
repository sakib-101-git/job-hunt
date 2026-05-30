from datetime import datetime
from pydantic import BaseModel


class ScrapedJob(BaseModel):
    source: str
    source_job_id: str
    title: str
    company: str
    location: str = ""
    posted_date: datetime | None = None
    url: str
    jd_text: str = ""
    salary_range: str | None = None
    job_type: str | None = None
    db_id: int | None = None
    is_remote: bool = False


class ScoredJob(ScrapedJob):
    fit_score: int = 0
    fit_reason: str = ""
