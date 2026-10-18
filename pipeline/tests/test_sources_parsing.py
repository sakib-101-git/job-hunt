"""Every source, end to end against responses recorded live on 2026-10-18
(tests/fixtures/sources, trimmed; emails and phone numbers scrubbed)."""

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.sources import FetchContext, build_source
from jobhunt.sources.http import Fetcher
from jobhunt.sources.models import SourceResult
from jobhunt.sources.recording import ReplayTransport

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


async def _no_sleep(_: float) -> None:
    return None


async def _fetch(
    config: Config, name: str, *, known: frozenset[str] = frozenset(), bootstrap: bool = False
) -> tuple[SourceResult, ReplayTransport]:
    transport = ReplayTransport(FIXTURES / name)
    fetcher = Fetcher(config.sources.http, httpx.AsyncClient(transport=transport), _no_sleep)
    source = build_source(name, config.sources.sources[name])
    assert source is not None
    ctx = FetchContext(fetcher, NOW, bootstrap, known, {})
    return await source.fetch(ctx), transport


async def test_every_listing_is_well_formed(config: Config) -> None:
    configured = set(config.sources.sources)
    for name in sorted(p.name for p in FIXTURES.iterdir() if p.name in configured):
        result, _ = await _fetch(config, name)
        assert result.listings, name
        for listing in result.listings:
            assert listing.source == name
            assert listing.source_job_id
            assert listing.title
            assert listing.description
            assert listing.url.startswith("https://")
            assert listing.posted_at is None or listing.posted_at.tzinfo == UTC
        ids = [listing.source_job_id for listing in result.listings]
        assert len(ids) == len(set(ids)), f"{name} returned duplicates"


async def test_bdjobs(config: Config) -> None:
    result, transport = await _fetch(config, "bdjobs")
    assert len(result.listings) == 4
    job = result.listings[0]
    assert job.source_job_id == "1546635"
    assert job.title == "Assistant Director (IT)"
    assert job.company == "University of Scholars"
    assert job.location == "Dhaka (Banani)"
    assert job.work_mode == "Work at office"
    assert job.employment_type == "Full Time"
    assert job.posted_at == datetime(2026, 10, 18, 8, 14, tzinfo=UTC)
    assert job.posted_at_source == "api"
    assert job.deadline == date(2026, 11, 17)
    assert job.url == "https://bdjobs.com/h/details/1546635"
    assert job.extra["category_id"] == "8"
    assert "Firewall" in job.extra["skills"]
    assert "<h3>Responsibilities</h3>" in job.description
    # One search page plus one details request per job.
    assert sum("GetJobSearch" in r for r in transport.requests) == 1
    assert sum("jobDetails" in r for r in transport.requests) == 4
    assert all("postedWithin=2" in r for r in transport.requests if "GetJobSearch" in r)


async def test_bdjobs_skips_details_for_known_jobs(config: Config) -> None:
    result, transport = await _fetch(config, "bdjobs", known=frozenset({"1546635", "1546715"}))
    assert len(result.listings) == 4
    assert sum("jobDetails" in r for r in transport.requests) == 2
    known = next(job for job in result.listings if job.source_job_id == "1546635")
    assert known.extra["details"] == "missing"


async def test_remoteok_skips_legal_notice(config: Config) -> None:
    result, _ = await _fetch(config, "remoteok")
    assert len(result.listings) == 5
    first = result.listings[0]
    assert (first.source_job_id, first.company) == ("1137542", "Curtins")
    # No location published: it stays unknown instead of becoming "Worldwide" (D4).
    assert first.location is None
    assert first.location_restrictions == ()


async def test_weworkremotely_region_and_company(config: Config) -> None:
    result, transport = await _fetch(config, "weworkremotely")
    assert len(transport.requests) == len(config.sources.sources["weworkremotely"].feeds)  # type: ignore[union-attr]
    job = next(j for j in result.listings if j.title == "Web Designer, eCRM")
    assert job.company == "Humane World for Animals"
    assert job.location_restrictions == ("Anywhere in the World",)
    assert job.posted_at_source == "rss"
    assert any("expires_at" in listing.extra for listing in result.listings)


async def test_jobicy(config: Config) -> None:
    result, _ = await _fetch(config, "jobicy")
    job = result.listings[0]
    assert (job.source_job_id, job.location_restrictions) == ("146254", ("Anywhere",))
    assert job.employment_type == "Full-Time"
    assert job.extra["level"] == "Senior"


async def test_workingnomads(config: Config) -> None:
    result, _ = await _fetch(config, "workingnomads")
    job = result.listings[0]
    assert job.source_job_id == "1944774"
    assert job.location_restrictions == ("USA, Canada, Ireland or UK only",)


async def test_himalayas_pages_until_empty(config: Config) -> None:
    result, transport = await _fetch(config, "himalayas")
    assert len(transport.requests) == 3  # two pages of jobs, then an empty page
    assert len(result.listings) == 6
    job = result.listings[0]
    assert job.url.startswith("https://himalayas.app/companies/")
    assert job.extra["location_policy"] == "worldwide"
    assert job.location_restrictions == ()


async def test_remotive(config: Config) -> None:
    result, _ = await _fetch(config, "remotive")
    job = result.listings[0]
    assert job.location_restrictions == ("USA", "Canada", "USA timezones")
    assert job.salary == "$170k - $200k"


def test_disabled_kinds_have_no_implementation(config: Config) -> None:
    for name, settings in config.sources.sources.items():
        if settings.kind == "disabled":
            assert build_source(name, settings) is None
