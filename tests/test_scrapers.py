"""
Scraper tests — parse saved HTML fixtures offline, assert correct field extraction.
Run fixtures by doing one live scrape first; they're saved automatically.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

FIXTURES = Path(__file__).parent / "fixtures"


def _make_config():
    cfg = MagicMock()
    cfg.search_keywords = ["python developer", "software engineer"]
    cfg.search_locations = ["dhaka", "remote"]
    cfg.exclude_keywords = ["principal"]
    cfg.exclude_companies = []
    cfg.max_age_hours = 72
    cfg.min_fit_score = 6
    cfg.request_delay = 0
    cfg.user_agent = "Test/1.0"
    cfg.max_retries = 0
    cfg.timeout_seconds = 10
    return cfg


# ────────────────────────────────────────────────────────────────────
# BDJobs

# BDJobs now uses the JSON search API (api.bdjobs.com/Jobs/api/JobSearch),
# so we test field extraction from a representative API item offline.
_BDJOBS_API_ITEM = {
    "Jobid": "1483986",
    "jobTitle": "Trainee Software Engineer",
    "companyName": "Data Edge Limited",
    "location": "Baridhara J Block",
    "publishDate": "2026-05-03T03:51:00Z",
    "jobContext": "<p>Recent graduate with B.Sc. in CSE.</p>",
    "jobDescription": "Bachelor of Science (BSc) in Computer Science",
    "eduRec": "Bachelor of Science (BSc) in Computer Science",
    "experience": "0 to 1 years",
    "JobType": "FullTime",
    "WorkPlace": "Office",
    "Salary": {"SalaryRange": None, "MinSalary": 20000, "MaxSalary": 25000,
               "IsNegotiable": False, "HideSalary": False},
}


def test_bdjobs_parse_job():
    from src.scrapers.bdjobs import BDJobsScraper
    scraper = BDJobsScraper(_make_config())
    job = scraper._parse_job(_BDJOBS_API_ITEM)
    assert job is not None
    assert job.source == "bdjobs"
    assert job.source_job_id == "1483986"
    assert job.title == "Trainee Software Engineer"
    assert job.company == "Data Edge Limited"
    assert job.url == "https://bdjobs.com/h/details/1483986"
    assert job.posted_date is not None and job.posted_date.year == 2026
    assert job.salary_range == "20000-25000"
    assert job.job_type == "FullTime"
    assert job.is_remote is False
    assert len(job.jd_text) > 50, "JD text should have meaningful content"


def test_bdjobs_parse_job_remote_and_missing_fields():
    from src.scrapers.bdjobs import BDJobsScraper
    scraper = BDJobsScraper(_make_config())
    # Missing Jobid/title -> dropped
    assert scraper._parse_job({"jobTitle": "", "Jobid": ""}) is None
    # WorkPlace "Home" -> remote; missing company -> Confidential
    job = scraper._parse_job({"Jobid": "1", "jobTitle": "Dev", "WorkPlace": "Home"})
    assert job.is_remote is True
    assert job.company == "Confidential"


# ────────────────────────────────────────────────────────────────────
# Shomvob

@pytest.mark.skipif(
    not (FIXTURES / "shomvob_search_sample.html").exists(),
    reason="No Shomvob fixture",
)
def test_shomvob_parse_listing():
    from src.scrapers.shomvob import ShomvobScraper
    scraper = ShomvobScraper(_make_config())
    html = (FIXTURES / "shomvob_search_sample.html").read_text(encoding="utf-8")
    jobs = scraper._parse_listing(html)
    assert isinstance(jobs, list)
    if jobs:
        assert jobs[0].source == "shomvob"


# ────────────────────────────────────────────────────────────────────
# Skill.jobs

# Skill.jobs now uses the studio.skill.jobs JSON API; test field extraction offline.
_SKILLJOBS_API_ITEM = {
    "id": 8890,
    "slug": "full-stack-developer-team-leader-3PCY8lCq",
    "title": "Full Stack Developer (Team Leader)",
    "type": "Full Time",
    "company_info": {"name": "Nexogs Systems Ltd.", "slug": "nexogs-01P8awaX"},
    "location": "Dhaka, Bangladesh",
    "division": "Dhaka",
    "min_salary": 0.0,
    "max_salary": 0.0,
    "isNegotiable": True,
    "workplace": "Work From Office",
    "created_at": "May 19, 2026",
}


def test_skilljobs_parse_job():
    from src.scrapers.skilljobs import SkillJobsScraper
    scraper = SkillJobsScraper(_make_config())
    job = scraper._parse_job(_SKILLJOBS_API_ITEM)
    assert job is not None
    assert job.source == "skilljobs"
    assert job.source_job_id == "8890"
    assert job.company == "Nexogs Systems Ltd."
    assert job.url == "https://skill.jobs/jobs/full-stack-developer-team-leader-3PCY8lCq"
    assert job.salary_range == "Negotiable"
    assert job.is_remote is False
    # Missing id/slug/title -> dropped
    assert scraper._parse_job({"id": "", "slug": "", "title": ""}) is None
    # Home workplace -> remote
    remote = scraper._parse_job({"id": "2", "slug": "x", "title": "Dev", "workplace": "Work From Home"})
    assert remote.is_remote is True
