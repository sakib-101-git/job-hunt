"""Edge cases the recorded fixtures don't contain."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.sources import FetchContext, build_source
from jobhunt.sources.http import Fetcher, FetchError
from jobhunt.sources.models import SourceResult

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]
NOW = datetime(2026, 10, 18, tzinfo=UTC)


async def _no_sleep(_: float) -> None:
    return None


async def _run(
    config: Config,
    name: str,
    handler: Handler,
    *,
    environ: dict[str, str] | None = None,
    state: dict[str, Any] | None = None,
    bootstrap: bool = False,
) -> SourceResult:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(config.sources.http.model_copy(update={"max_retries": 0}), client, _no_sleep)
    source = build_source(name, config.sources.sources[name])
    assert source is not None
    ctx = FetchContext(fetcher, NOW, bootstrap, frozenset(), environ or {}, state or {})
    return await source.fetch(ctx)


def _bdjobs_handler(details: dict[str, httpx.Response], pages: list[dict[str, Any]]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if "GetJobSearch" in str(request.url):
            return httpx.Response(200, json=pages[int(request.url.params["pg"]) - 1])
        return details[request.url.params["jobId"]]

    return handler


def _job(job_id: str, title: str = "Software Engineer") -> dict[str, Any]:
    return {"Jobid": job_id, "jobTitle": title, "publishDate": "2026-10-18T00:00:00Z"}


async def test_bdjobs_pages_drops_closed_and_survives_failed_details(config: Config) -> None:
    pages = [
        {"data": [_job("1"), _job("2")], "premiumData": [_job("1")], "common": {"totalpages": 2}},
        {"data": [_job("3"), {"Jobid": "4", "jobTitle": ""}], "common": {"totalpages": 2}},
    ]
    details = {
        "1": httpx.Response(200, json={"data": [{"JobFound": "True", "Closed": "1"}]}),
        "2": httpx.Response(500),
        "3": httpx.Response(200, json={"data": [{"JobFound": "False"}]}),
    }
    result = await _run(config, "bdjobs", _bdjobs_handler(details, pages), bootstrap=True)
    assert [job.source_job_id for job in result.listings] == ["2", "3"]
    assert all(job.extra["details"] == "missing" for job in result.listings)


async def test_bdjobs_bootstrap_has_no_posted_within(config: Config) -> None:
    seen: list[httpx.Request] = []
    page = {"data": [], "common": {"totalpages": 0}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=page)

    assert (await _run(config, "bdjobs", handler, bootstrap=True)).listings == []
    assert "postedWithin" not in seen[0].url.params


async def test_json_feed_unchanged_and_incomplete_items(config: Config) -> None:
    def not_modified(_: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    state = {"validators": {"https://jobicy.com/api/v2/remote-jobs": {"etag": "e"}}}
    result = await _run(config, "jobicy", not_modified, state=state)
    assert result.unchanged
    assert result.listings == []

    def incomplete(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [{"id": "1"}, {"jobTitle": "x", "url": "u"}]})

    assert (await _run(config, "jobicy", incomplete)).listings == []


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("remoteok", [{"legal": "x"}, {"id": "1", "position": "Dev"}]),
        ("workingnomads", [{"url": "https://www.workingnomads.com/jobs/x", "title": "Dev"}]),
        ("remotive", {"jobs": [{"id": "1", "url": "u"}]}),
    ],
)
async def test_items_without_required_fields_are_skipped(
    config: Config, name: str, body: object
) -> None:
    result = await _run(config, name, lambda _: httpx.Response(200, json=body))
    assert result.listings == []


async def test_remoteok_salary(config: Config) -> None:
    item = {"id": "1", "position": "Dev", "url": "https://remoteok.com/x", "salary_min": "50000",
            "salary_max": "70000", "tags": ["python"]}  # fmt: skip
    result = await _run(config, "remoteok", lambda _: httpx.Response(200, json=[item]))
    assert result.listings[0].salary == "50000-70000 USD/year"
    assert result.listings[0].tags == ("python",)


async def test_rss_unchanged_invalid_and_titles(config: Config) -> None:
    def not_modified(_: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    assert (await _run(config, "weworkremotely", not_modified)).unchanged

    with pytest.raises(FetchError, match="invalid RSS"):
        await _run(config, "weworkremotely", lambda _: httpx.Response(200, text="<rss><oops"))

    feed = """<rss><channel>
      <item><title>No Company Title</title><link>https://wwr/a</link></item>
      <item><title>Missing link</title></item>
    </channel></rss>"""
    result = await _run(config, "weworkremotely", lambda _: httpx.Response(200, text=feed))
    assert [(job.company, job.title) for job in result.listings] == [(None, "No Company Title")]
    assert result.listings[0].location is None


async def test_himalayas_key_and_string_encoded_lists(config: Config) -> None:
    seen: list[httpx.Request] = []
    job = {
        "guid": "https://himalayas.app/j/1",
        "title": "Backend Engineer",
        "locationRestrictions": json.dumps(["Bangladesh", "India"]),
        "seniority": "Senior",
        "minSalary": "1000",
        "currency": "USD",
        "salaryPeriod": "month",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        jobs = [job, {"title": "no guid"}] if request.url.params["page"] == "1" else []
        return httpx.Response(200, json={"jobs": jobs})

    result = await _run(config, "himalayas", handler, environ={"HIMALAYAS_API_KEY": "k"})
    assert seen[0].headers["authorization"] == "Bearer k"
    [listing] = result.listings
    assert listing.location_restrictions == ("Bangladesh", "India")
    assert "location_policy" not in listing.extra
    assert listing.extra["seniority"] == "Senior"
    assert listing.salary == "1000-? USD month"
    assert listing.url == "https://himalayas.app/j/1"


async def test_himalayas_without_key_sends_no_authorization(config: Config) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jobs": [], "nope": 1})

    await _run(config, "himalayas", handler)
    assert "authorization" not in seen[0].headers
