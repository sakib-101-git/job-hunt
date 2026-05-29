from datetime import datetime
from pydantic import BaseModel, Field


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


class ScoredJob(ScrapedJob):
    fit_score: int = 0
    fit_reason: str = ""


class TailoredExperience(BaseModel):
    company: str
    role: str
    dates: str
    location: str = ""
    selected_bullets: list[str] = Field(default_factory=list)


class TailoredProject(BaseModel):
    name: str
    url: str = ""
    selected_bullets: list[str] = Field(default_factory=list)


class TailoredCV(BaseModel):
    job_url: str = ""
    headline: str
    selected_summary: str
    experience: list[TailoredExperience] = Field(default_factory=list)
    projects: list[TailoredProject] = Field(default_factory=list)
    selected_skills: dict[str, list[str]] = Field(default_factory=dict)
    education: list[dict] = Field(default_factory=list)
    certifications: list[dict] = Field(default_factory=list)


class TailoredCoverLetter(BaseModel):
    greeting: str = "Dear Hiring Manager,"
    body: str
    closing: str = "Best regards,"
