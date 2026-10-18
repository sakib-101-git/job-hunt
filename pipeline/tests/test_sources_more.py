"""Company boards, HN "Who is hiring" and LinkedIn alert emails."""

from datetime import UTC, datetime
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from pathlib import Path
from typing import Any

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.config.models import AtsCompany, AtsSource, ImapSource
from jobhunt.sources import FetchContext, build_source
from jobhunt.sources.ats import CompanyBoards
from jobhunt.sources.http import Fetcher, FetchError
from jobhunt.sources.imap import LinkedInAlerts, parse_alert
from jobhunt.sources.models import SourceResult
from jobhunt.sources.recording import ReplayTransport

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
IMAP_ENV = {"IMAP_HOST": "imap.test", "IMAP_EMAIL": "me@test", "IMAP_APP_PASSWORD": "pw"}


async def _no_sleep(_: float) -> None:
    return None


def _ctx(transport: httpx.AsyncBaseTransport, config: Config, **kw: Any) -> FetchContext:
    fetcher = Fetcher(
        config.sources.http.model_copy(update={"max_retries": 0}),
        httpx.AsyncClient(transport=transport),
        _no_sleep,
    )
    return FetchContext(fetcher, NOW, False, kw.get("known", frozenset()), kw.get("environ", {}))


async def _replay(
    config: Config, name: str, source: Any = None
) -> tuple[SourceResult, ReplayTransport]:
    transport = ReplayTransport(FIXTURES / "sources" / name)
    source = source or build_source(name, config.sources.sources[name])
    return await source.fetch(_ctx(transport, config)), transport


# --- company boards -----------------------------------------------------------


async def test_company_boards_from_every_ats(config: Config) -> None:
    result, transport = await _replay(config, "company_boards")
    assert result.warnings == ()
    by_company: dict[str | None, list[Any]] = {}
    for listing in result.listings:
        by_company.setdefault(listing.company, []).append(listing)
    assert set(by_company) == {"Populate", "Canonical", "Supabase", "Toptal", "Polygon Technology"}

    populate = by_company["Populate"]
    assert all("Bangladesh" in (j.location or "") for j in populate)
    assert populate[0].source_job_id.startswith("greenhouse:applytopopulate:")
    assert "<p>" in populate[0].description  # HTML entities are decoded

    canonical = by_company["Canonical"][0]
    assert "Home based - Worldwide" in canonical.location_restrictions

    supabase = by_company["Supabase"][0]
    assert supabase.location_restrictions[0] == "Remote, Global"

    toptal = by_company["Toptal"]
    assert any("Anywhere" in j.location_restrictions for j in toptal)
    assert toptal[0].posted_at is not None
    assert toptal[0].posted_at.year == 2026

    polygon = by_company["Polygon Technology"][0]
    assert polygon.location == "Dhaka, BD"
    assert polygon.url.startswith("https://jobs.smartrecruiters.com/PolygonTechnology/")
    assert "Frontend" in polygon.description
    # The SmartRecruiters description came from one details request.
    assert sum("/postings/7" in r for r in transport.requests) == 1


async def test_workable_widget(config: Config) -> None:
    settings = config.sources.sources["company_boards"]
    assert isinstance(settings, AtsSource)
    sample = settings.model_copy(
        update={
            "companies": [
                AtsCompany(
                    name="Hugging Face",
                    ats="workable",
                    token="huggingface",  # noqa: S106 (a board name, not a secret)
                    verified="test",
                )
            ]
        }
    )
    result, _ = await _replay(config, "workable_sample", CompanyBoards("company_boards", sample))
    job = result.listings[0]
    assert job.source_job_id.startswith("workable:huggingface:")
    assert job.url.startswith("https://apply.workable.com/j/")
    assert job.work_mode == "remote"
    assert job.location_restrictions
    assert job.description.startswith("<p>")


async def test_one_failing_board_is_a_warning(config: Config) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "canonical" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(
            200, json={"jobs": [], "content": []} if "lever" not in str(request.url) else []
        )

    settings = config.sources.sources["company_boards"]
    result = await build_source("company_boards", settings).fetch(  # type: ignore[union-attr]
        _ctx(httpx.MockTransport(handler), config)
    )
    assert len(result.warnings) == 1
    assert "Canonical" in result.warnings[0]


async def test_every_failing_board_fails_the_source(config: Config) -> None:
    source = build_source("company_boards", config.sources.sources["company_boards"])
    with pytest.raises(FetchError, match="every company board failed"):
        await source.fetch(_ctx(httpx.MockTransport(lambda _: httpx.Response(500)), config))  # type: ignore[union-attr]


async def test_known_smartrecruiters_jobs_skip_details(config: Config) -> None:
    transport = ReplayTransport(FIXTURES / "sources" / "company_boards")
    source = build_source("company_boards", config.sources.sources["company_boards"])
    known = frozenset({"smartrecruiters:PolygonTechnology:744000156124949"})
    await source.fetch(_ctx(transport, config, known=known))  # type: ignore[union-attr]
    assert not any("/postings/7" in r for r in transport.requests)


def test_board_settings_are_checked(config: Config) -> None:
    settings = config.sources.sources["company_boards"]
    assert isinstance(settings, AtsSource)
    data = settings.model_dump()
    data["companies"].append(dict(data["companies"][0]))
    with pytest.raises(ValueError, match="duplicate companies"):
        AtsSource.model_validate(data)
    data = settings.model_dump()
    del data["boards"]["lever"]
    with pytest.raises(ValueError, match="no board URL for lever"):
        AtsSource.model_validate(data)


# --- Hacker News --------------------------------------------------------------


async def test_hn_keeps_only_remote_worldwide_top_level_posts(config: Config) -> None:
    result, transport = await _replay(config, "hn_whoishiring")
    assert len(transport.requests) == 2
    assert result.listings
    for listing in result.listings:
        assert "REMOTE" in listing.description
        assert listing.url.startswith("https://news.ycombinator.com/item?id=")
        assert not listing.title.startswith("http")
        assert listing.extra["thread"].startswith("Ask HN: Who is hiring?")
    ids = {listing.source_job_id for listing in result.listings}
    assert "50104234" not in ids  # Helsinki on-site post


async def test_hn_without_a_thread(config: Config) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [{"title": "Ask HN: Who wants to be hired?"}]})

    source = build_source("hn_whoishiring", config.sources.sources["hn_whoishiring"])
    result = await source.fetch(_ctx(httpx.MockTransport(handler), config))  # type: ignore[union-attr]
    assert result.listings == []
    assert result.warnings


# --- LinkedIn alert emails ----------------------------------------------------


def test_parse_alert() -> None:
    raw = (FIXTURES / "emails" / "linkedin_alert_synthetic.eml").read_bytes()
    message = message_from_bytes(raw, policy=default)
    assert isinstance(message, EmailMessage)
    body = message.get_body(preferencelist=("html",))
    assert body is not None
    jobs = parse_alert(body.get_content())
    assert jobs == [
        ("4100000001", "Junior Software Engineer", "Example Tech Ltd", "Dhaka, Bangladesh"),
        ("4100000002", "SQA Engineer", "Sample Soft", "Chattogram, Bangladesh (On-site)"),
        ("4100000003", "Machine Learning Engineer", "Demo AI", None),
    ]


class FakeImap:
    def __init__(self, host: str) -> None:
        self.host = host
        self.calls: list[tuple[Any, ...]] = []

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))

    def select(self, mailbox: str, readonly: bool) -> None:
        self.calls.append(("select", mailbox, readonly))

    def search(self, charset: None, *criteria: str) -> tuple[str, list[bytes]]:
        self.calls.append(("search", *criteria))
        return "OK", [b"1 2"]

    def fetch(self, number: str, parts: str) -> tuple[str, list[object]]:
        raw = (FIXTURES / "emails" / "linkedin_alert_synthetic.eml").read_bytes()
        return "OK", [(b"1 (RFC822 {n}", raw), b")"]

    def logout(self) -> None:
        self.calls.append(("logout",))


async def test_linkedin_alerts_read_only_and_deduplicated(config: Config) -> None:
    settings = config.sources.sources["linkedin_alerts"]
    assert isinstance(settings, ImapSource)
    boxes: list[FakeImap] = []

    def connect(host: str) -> FakeImap:
        boxes.append(FakeImap(host))
        return boxes[-1]

    source = LinkedInAlerts("linkedin_alerts", settings, connect)
    result = await source.fetch(
        _ctx(httpx.MockTransport(lambda _: httpx.Response(500)), config, environ=IMAP_ENV)
    )
    assert [j.source_job_id for j in result.listings] == ["4100000001", "4100000002", "4100000003"]
    job = result.listings[0]
    assert job.url == "https://www.linkedin.com/jobs/view/4100000001/"
    assert job.extra["alert_sent_at"].startswith("2026-10-17T05:30:00")
    assert job.posted_at is None
    calls = boxes[0].calls
    assert ("select", "INBOX", True) in calls
    assert ("search", "FROM", '"jobs-noreply@linkedin.com"', "SINCE", "16-Oct-2026") in calls


async def test_linkedin_alerts_imap_failure(config: Config) -> None:
    settings = config.sources.sources["linkedin_alerts"]
    assert isinstance(settings, ImapSource)

    def refuse(host: str) -> FakeImap:
        raise OSError("connection refused")

    source = LinkedInAlerts("linkedin_alerts", settings, refuse)
    with pytest.raises(FetchError, match="IMAP failed: OSError"):
        await source.fetch(
            _ctx(httpx.MockTransport(lambda _: httpx.Response(500)), config, environ=IMAP_ENV)
        )


def test_registry_builds_new_kinds(config: Config) -> None:
    for name in ("company_boards", "hn_whoishiring", "linkedin_alerts"):
        assert build_source(name, config.sources.sources[name]) is not None
