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

@pytest.mark.skipif(
    not (FIXTURES / "bdjobs_search_sample.html").exists(),
    reason="No BDJobs fixture — run a live scrape first",
)
def test_bdjobs_parse_listing():
    from src.scrapers.bdjobs import BDJobsScraper
    scraper = BDJobsScraper(_make_config())
    html = (FIXTURES / "bdjobs_search_sample.html").read_text(encoding="utf-8")
    jobs = scraper._parse_listing(html)
    assert len(jobs) > 0, "Expected at least one job from fixture"
    job = jobs[0]
    assert job.source == "bdjobs"
    assert job.title, "title should not be empty"
    assert job.url.startswith("http"), "url should be absolute"
    assert job.source_job_id, "source_job_id should not be empty"


@pytest.mark.skipif(
    not (FIXTURES / "bdjobs_detail_sample.html").exists(),
    reason="No BDJobs detail fixture",
)
def test_bdjobs_parse_detail():
    from src.scrapers.bdjobs import BDJobsScraper
    scraper = BDJobsScraper(_make_config())
    html = (FIXTURES / "bdjobs_detail_sample.html").read_text(encoding="utf-8")
    text = scraper._parse_detail(html)
    assert len(text) > 50, "JD text should have meaningful content"


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

@pytest.mark.skipif(
    not (FIXTURES / "skilljobs_search_sample.html").exists(),
    reason="No SkillJobs fixture",
)
def test_skilljobs_parse_listing():
    from src.scrapers.skilljobs import SkillJobsScraper
    scraper = SkillJobsScraper(_make_config())
    html = (FIXTURES / "skilljobs_search_sample.html").read_text(encoding="utf-8")
    jobs = scraper._parse_listing(html)
    assert isinstance(jobs, list)
    if jobs:
        assert jobs[0].source == "skilljobs"
