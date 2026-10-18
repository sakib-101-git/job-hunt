import asyncio
import gzip
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from jobhunt.config import Config
from jobhunt.config.models import HostOverride, HttpSettings
from jobhunt.sources.base import FetchContext
from jobhunt.sources.http import Fetcher, FetchError, Validators
from jobhunt.sources.recording import RecordingTransport, ReplayTransport

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def _settings(config: Config, **changes: object) -> HttpSettings:
    return config.sources.http.model_copy(update=changes)


def _fetcher(settings: HttpSettings, handler: Handler, clock: Clock) -> Fetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Fetcher(settings, client, sleep=clock.sleep, clock=clock, jitter=lambda: 0.5)


def _sequence(*responses: httpx.Response | Exception) -> Handler:
    queue = list(responses)

    def handler(_: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


async def test_sends_user_agent_and_params(config: Config) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    fetcher = _fetcher(_settings(config), handler, Clock())
    response = await fetcher.get("https://a.test/x", params={"q": "1"}, headers={"X-A": "b"})
    assert response.json() == {"ok": True}
    assert seen[0].headers["user-agent"] == config.sources.http.user_agent
    assert seen[0].headers["x-a"] == "b"
    assert seen[0].url.params["q"] == "1"


async def test_retries_with_backoff_and_retry_after(config: Config) -> None:
    clock = Clock()
    settings = _settings(config, max_retries=3, backoff_base_seconds=2, backoff_max_seconds=60)
    handler = _sequence(
        httpx.Response(503),
        httpx.Response(429, headers={"retry-after": "5"}),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, text="ok"),
    )
    response = await _fetcher(settings, handler, clock).get("https://a.test/")
    assert response.text == "ok"
    # Jittered exponential backoff (jitter fixed at 0.5): 2*0.75, Retry-After, 8*0.75.
    assert clock.sleeps == [1.5, 5.0, 6.0]


async def test_retry_after_is_capped(config: Config) -> None:
    clock = Clock()
    settings = _settings(config, max_retries=1, backoff_max_seconds=10)
    handler = _sequence(httpx.Response(429, headers={"retry-after": "3600"}), httpx.Response(200))
    await _fetcher(settings, handler, clock).get("https://a.test/")
    assert clock.sleeps == [10.0]


async def test_gives_up_after_max_retries(config: Config) -> None:
    settings = _settings(config, max_retries=2)
    handler = _sequence(*[httpx.Response(500) for _ in range(3)])
    with pytest.raises(FetchError, match="HTTP 500") as exc:
        await _fetcher(settings, handler, Clock()).get("https://a.test/")
    assert exc.value.status == 500

    handler = _sequence(*[httpx.ConnectError("down") for _ in range(3)])
    with pytest.raises(FetchError, match="network error"):
        await _fetcher(settings, handler, Clock()).get("https://a.test/")


async def test_other_client_errors_fail_fast(config: Config) -> None:
    clock = Clock()
    with pytest.raises(FetchError, match="HTTP 404"):
        await _fetcher(_settings(config), _sequence(httpx.Response(404)), clock).get("https://a/")
    assert clock.sleeps == []


async def test_decoding_errors_become_fetch_errors(config: Config) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"not gzip")

    with pytest.raises(FetchError, match="DecodingError"):
        await _fetcher(_settings(config), handler, Clock()).get("https://a.test/")


async def test_non_json_body(config: Config) -> None:
    response = await _fetcher(
        _settings(config), _sequence(httpx.Response(200, text="<html>")), Clock()
    ).get("https://a.test/")
    with pytest.raises(FetchError, match="not JSON"):
        response.json()


async def test_requests_to_one_host_are_spaced(config: Config) -> None:
    clock = Clock()
    settings = _settings(config, per_host_min_delay_seconds=2.0, host_overrides={})
    fetcher = _fetcher(settings, lambda _: httpx.Response(200), clock)
    await fetcher.get("https://a.test/1")
    await fetcher.get("https://a.test/2")
    await fetcher.get("https://b.test/1")  # another host: no wait
    assert clock.sleeps == [2.0]


async def test_host_override_limits_concurrency(config: Config) -> None:
    active = 0
    peak = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200)

    settings = _settings(
        config,
        per_host_concurrency=5,
        per_host_min_delay_seconds=0,
        host_overrides={"slow.test": HostOverride(concurrency=1, min_delay_seconds=0)},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(settings, client)
    await asyncio.gather(*(fetcher.get(f"https://slow.test/{i}") for i in range(4)))
    assert peak == 1


async def test_conditional_requests(config: Config) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"', "last-modified": "Sat"}, text="x")

    fetcher = _fetcher(_settings(config), handler, Clock())
    ctx = FetchContext(fetcher, None, False, frozenset(), {})  # type: ignore[arg-type]
    first = await ctx.get_if_changed("https://a.test/feed")
    assert first is not None
    assert ctx.state["validators"]["https://a.test/feed"] == {
        "etag": '"v1"',
        "last_modified": "Sat",
    }
    assert await ctx.get_if_changed("https://a.test/feed") is None
    assert seen[1].headers["if-modified-since"] == "Sat"


async def test_validators_are_dropped_when_server_stops_sending_them(config: Config) -> None:
    fetcher = _fetcher(_settings(config), lambda _: httpx.Response(200), Clock())
    ctx = FetchContext(fetcher, None, False, frozenset(), {})  # type: ignore[arg-type]
    ctx.state["validators"] = {"https://a.test/": {"etag": "old"}}
    await ctx.get_if_changed("https://a.test/")
    assert ctx.state["validators"] == {}


def test_validators_round_trip() -> None:
    validators = Validators("e", None)
    assert Validators.from_dict(validators.as_dict()) == validators


async def test_owned_client_is_closed(config: Config) -> None:
    await Fetcher(config.sources.http).aclose()


async def test_record_then_replay(tmp_path: Path) -> None:
    def live(request: httpx.Request) -> httpx.Response:
        body = gzip.compress(f"page {request.url.params['p']}".encode())
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=body)

    recorder = RecordingTransport(tmp_path, inner=httpx.MockTransport(live))
    async with httpx.AsyncClient(transport=recorder) as client:
        first = await client.get("https://a.test/x", params={"p": "1", "a": "z"})
        await client.get("https://a.test/x", params={"p": "2"})
    assert first.text == "page 1"

    replay = ReplayTransport(tmp_path)
    async with httpx.AsyncClient(transport=replay) as client:
        # Parameter order does not matter.
        again = await client.get("https://a.test/x", params={"a": "z", "p": "1"})
        assert again.text == "page 1"
        with pytest.raises(httpx.ConnectError, match="no recording"):
            await client.get("https://a.test/x", params={"p": "3"})
