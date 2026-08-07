from __future__ import annotations

import random
from dataclasses import dataclass, field

import pytest

from worker.fetch.client import (
    CHROME_UA,
    HONEST_UA,
    Blocked,
    BudgetExhausted,
    Challenged,
    Disallowed,
    FetchFailed,
    PoliteClient,
    Policy,
    Response,
)

ROBOTS_OPEN = b"User-agent: *\nDisallow: /api/*\n"
CHALLENGE_PAGE = b"<html><body>Just a moment... Enable JavaScript and cookies to continue</body></html>"


@dataclass
class Recorded:
    url: str
    headers: dict[str, str]
    impersonate: str | None


@dataclass
class FakeTransport:
    """Returns queued responses and records what was asked for."""

    responses: dict[str, list[Response]] = field(default_factory=dict)
    default: Response | None = None
    calls: list[Recorded] = field(default_factory=list)
    raise_on: set[str] = field(default_factory=set)

    def get(self, url, *, headers, impersonate, timeout):  # noqa: ANN001, ANN201
        self.calls.append(Recorded(url, dict(headers), impersonate))
        if url in self.raise_on:
            raise ConnectionError("connection reset")
        queue = self.responses.get(url)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        if url.endswith("/robots.txt"):
            return Response(200, ROBOTS_OPEN)
        if self.default is not None:
            return self.default
        return Response(200, b"<html>ok</html>")


@dataclass
class FakeClock:
    """Time only advances when something sleeps, so pacing is deterministic."""

    now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(transport: FakeTransport, **policy: object) -> tuple[PoliteClient, FakeClock]:
    clock = FakeClock()
    client = PoliteClient(
        transport,
        policy=Policy(**policy),  # type: ignore[arg-type]
        clock=clock,
        sleeper=clock.sleep,
        rng=random.Random(1),
    )
    return client, clock


# ── robots ────────────────────────────────────────────────────────────────


def test_disallowed_path_is_never_requested() -> None:
    transport = FakeTransport()
    client, _ = make_client(transport)
    with pytest.raises(Disallowed):
        client.get("https://example.com/api/secret")
    assert [c.url for c in transport.calls] == ["https://example.com/robots.txt"]


def test_robots_is_read_with_our_real_identity() -> None:
    """Permission is about who we are, not what a header claims."""
    transport = FakeTransport()
    client, _ = make_client(transport, user_agent_mode="chrome")
    client.get("https://example.com/page")
    robots_call = next(c for c in transport.calls if c.url.endswith("robots.txt"))
    assert robots_call.headers["User-Agent"] == HONEST_UA
    # No impersonation either: an honest header over a masked handshake is not
    # honest, and a host can serve a different file to each.
    assert robots_call.impersonate is None
    page_call = next(c for c in transport.calls if c.url.endswith("/page"))
    assert page_call.impersonate is not None


def test_robots_is_fetched_once_per_host() -> None:
    transport = FakeTransport()
    client, _ = make_client(transport)
    for path in ("/a", "/b", "/c"):
        client.get(f"https://example.com{path}")
    assert sum(1 for c in transport.calls if c.url.endswith("robots.txt")) == 1


def test_unavailable_robots_does_not_block_everything() -> None:
    """Per RFC 9309 a 4xx means no rules. It is not read as a prohibition."""
    transport = FakeTransport(
        responses={"https://example.com/robots.txt": [Response(404, b"")]}
    )
    client, _ = make_client(transport)
    assert client.get("https://example.com/page").status == 200


def test_crawl_delay_raises_the_pace() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/robots.txt": [
                Response(200, b"User-agent: *\nCrawl-delay: 30\n")
            ]
        }
    )
    client, clock = make_client(transport, rate_limit_rps=10.0)
    client.get("https://example.com/a")
    client.get("https://example.com/b")
    assert clock.slept and min(clock.slept) >= 30


# ── challenges and refusals ───────────────────────────────────────────────


def test_challenge_stops_the_run_and_is_not_retried() -> None:
    """A challenge asks for a human. Retrying or working around it is out of scope."""
    transport = FakeTransport(
        responses={"https://example.com/p": [Response(403, CHALLENGE_PAGE)]}
    )
    client, _ = make_client(transport)
    with pytest.raises(Challenged):
        client.get("https://example.com/p")
    assert sum(1 for c in transport.calls if c.url == "https://example.com/p") == 1


def test_challenge_is_detected_from_the_header_alone() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/p": [
                Response(403, b"binary noise", {"cf-mitigated": "challenge"})
            ]
        }
    )
    client, _ = make_client(transport)
    with pytest.raises(Challenged):
        client.get("https://example.com/p")


def test_challenge_beats_a_403_so_no_escalation_happens() -> None:
    """A 403 may be a misreading worth correcting; a challenge is not."""
    transport = FakeTransport(
        responses={"https://example.com/p": [Response(403, CHALLENGE_PAGE)]}
    )
    client, _ = make_client(transport, user_agent_mode="auto")
    with pytest.raises(Challenged):
        client.get("https://example.com/p")
    assert client.stats.ua_escalations == 0


def test_a_403_escalates_the_user_agent_exactly_once() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/p": [Response(403, b"forbidden"), Response(200, b"<html>ok")]
        }
    )
    client, _ = make_client(transport, user_agent_mode="auto")
    assert client.get("https://example.com/p").status == 200

    attempts = [c for c in transport.calls if c.url == "https://example.com/p"]
    assert len(attempts) == 2
    assert attempts[0].headers["User-Agent"] == HONEST_UA
    assert attempts[1].headers["User-Agent"] == CHROME_UA
    assert client.stats.ua_escalations == 1


def test_a_second_403_stops_rather_than_cycling_agents() -> None:
    """One correction is fair; cycling values until one works is defeating a
    decision rather than fixing a misreading."""
    transport = FakeTransport(default=Response(403, b"forbidden"))
    client, _ = make_client(transport, user_agent_mode="auto")
    with pytest.raises(Blocked):
        client.get("https://example.com/p")
    assert client.stats.ua_escalations == 1
    assert len([c for c in transport.calls if c.url == "https://example.com/p"]) == 2


def test_honest_mode_does_not_escalate() -> None:
    transport = FakeTransport(default=Response(403, b"forbidden"))
    client, _ = make_client(transport, user_agent_mode="honest")
    with pytest.raises(Blocked):
        client.get("https://example.com/p")
    assert client.stats.ua_escalations == 0


def test_honest_mode_sends_no_impersonation() -> None:
    transport = FakeTransport()
    client, _ = make_client(transport, user_agent_mode="honest")
    client.get("https://example.com/p")
    page_call = next(c for c in transport.calls if c.url.endswith("/p"))
    assert page_call.impersonate is None
    assert page_call.headers["User-Agent"] == HONEST_UA


# ── retries ───────────────────────────────────────────────────────────────


def test_a_429_is_retried_and_honours_retry_after() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/p": [
                Response(429, b"slow down", {"retry-after": "42"}),
                Response(200, b"<html>ok"),
            ]
        }
    )
    client, clock = make_client(transport)
    assert client.get("https://example.com/p").status == 200
    assert 42 in clock.slept
    assert client.stats.retries == 1


def test_retries_are_bounded() -> None:
    transport = FakeTransport(default=Response(503, b"unavailable"))
    client, _ = make_client(transport, retry_max_attempts=2, retry_base_seconds=1)
    with pytest.raises(FetchFailed):
        client.get("https://example.com/p")
    assert len([c for c in transport.calls if c.url.endswith("/p")]) == 3


def test_backoff_grows() -> None:
    """Pacing is set fast here so the only long sleeps are the back-offs."""
    transport = FakeTransport(default=Response(500, b"boom"))
    client, clock = make_client(
        transport, retry_max_attempts=3, retry_base_seconds=2, rate_limit_rps=100
    )
    with pytest.raises(FetchFailed):
        client.get("https://example.com/p")
    page_sleeps = [s for s in clock.slept if s >= 1]
    assert page_sleeps == sorted(page_sleeps)
    assert len(page_sleeps) >= 2


def test_a_transport_error_is_a_fetch_failure() -> None:
    transport = FakeTransport(raise_on={"https://example.com/p"})
    client, _ = make_client(transport)
    with pytest.raises(FetchFailed):
        client.get("https://example.com/p")


# ── conditional requests ──────────────────────────────────────────────────


def test_validators_are_remembered_and_replayed() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/p": [
                Response(200, b"<html>v1", {"etag": '"abc"', "last-modified": "Mon, 01 Jan 2026"}),
                Response(304, b""),
            ]
        }
    )
    client, _ = make_client(transport)
    client.get("https://example.com/p")
    second = client.get("https://example.com/p")

    assert second.status == 304
    assert second.from_cache is True
    assert client.stats.not_modified == 1
    replay = [c for c in transport.calls if c.url.endswith("/p")][1]
    assert replay.headers["If-None-Match"] == '"abc"'
    assert replay.headers["If-Modified-Since"] == "Mon, 01 Jan 2026"


def test_conditional_requests_can_be_switched_off() -> None:
    transport = FakeTransport(
        responses={"https://example.com/p": [Response(200, b"x", {"etag": '"abc"'})]}
    )
    client, _ = make_client(transport, conditional_requests=False)
    client.get("https://example.com/p")
    client.get("https://example.com/p")
    assert "If-None-Match" not in transport.calls[-1].headers


# ── budget ────────────────────────────────────────────────────────────────


def test_request_ceiling_stops_the_run() -> None:
    """A pagination bug must not turn into a flood."""
    transport = FakeTransport()
    client, _ = make_client(transport, max_requests=4)
    with pytest.raises(BudgetExhausted):
        for i in range(20):
            client.get(f"https://example.com/p{i}")
    assert client.stats.requests <= 4


def test_time_ceiling_stops_the_run() -> None:
    transport = FakeTransport(
        responses={
            "https://example.com/robots.txt": [
                Response(200, b"User-agent: *\nCrawl-delay: 60\n")
            ]
        }
    )
    client, _ = make_client(transport, max_runtime_s=100, rate_limit_rps=10)
    with pytest.raises(BudgetExhausted):
        for i in range(20):
            client.get(f"https://example.com/p{i}")


# ── pacing and ordering ───────────────────────────────────────────────────


def test_no_delay_before_the_first_request_to_a_host() -> None:
    """Nothing is owed on arrival. The robots fetch is that first request, so the
    page after it is paced — two requests in immediate succession is the thing
    being avoided."""
    transport = FakeTransport()
    client, clock = make_client(transport, rate_limit_rps=0.1)
    client.get("https://example.com/p")
    assert len(clock.slept) == 1        # between robots.txt and the page
    assert clock.slept[0] > 0


def test_intervals_between_requests_are_uneven() -> None:
    """A perfectly regular cadence is one of the clearest signs of automation."""
    transport = FakeTransport()
    client, clock = make_client(transport, rate_limit_rps=1.0, max_requests=100)
    for i in range(12):
        client.get(f"https://example.com/p{i}")
    waits = [round(s, 6) for s in clock.slept if s > 0]
    assert len(waits) >= 8
    assert len(set(waits)) > 1


def test_pacing_is_clamped_to_the_configured_band() -> None:
    transport = FakeTransport()
    client, clock = make_client(
        transport, rate_limit_rps=1.0, jitter_min_factor=0.5, jitter_max_factor=2.0,
        max_requests=100,
    )
    for i in range(20):
        client.get(f"https://example.com/p{i}")
    waits = [s for s in clock.slept if s > 0]
    assert waits
    assert min(waits) >= 0.5 - 1e-9
    assert max(waits) <= 2.0 + 1e-9


def test_pacing_is_per_host() -> None:
    """A second host owes nothing: each is paced against its own last request."""
    transport = FakeTransport()
    client, clock = make_client(transport, rate_limit_rps=0.5)
    client.get("https://a.example.com/p")
    first_host_sleeps = len(clock.slept)
    client.get("https://b.example.com/p")
    # Only the robots-to-page gap on the new host, not a wait for the old one.
    assert len(clock.slept) == first_host_sleeps + 1


def test_order_shuffles_and_keeps_every_url() -> None:
    """Walking identifiers in order is recognisable, and the order means nothing."""
    transport = FakeTransport()
    client, _ = make_client(transport)
    urls = [f"https://example.com/{i}" for i in range(30)]
    shuffled = client.order(urls)
    assert sorted(shuffled) == sorted(urls)
    assert shuffled != urls


def test_order_can_be_switched_off() -> None:
    transport = FakeTransport()
    client, _ = make_client(transport, shuffle_urls=False)
    urls = [f"https://example.com/{i}" for i in range(10)]
    assert client.order(urls) == urls


# ── policy ────────────────────────────────────────────────────────────────


def test_policy_from_config() -> None:
    config = {
        "user_agent_mode": "honest",
        "rate_limit_rps": 0.25,
        "sweep_rate_limit_rps": 0.1,
        "jitter": {"min_factor": 0.3, "max_factor": 4.0},
        "retry": {"max_attempts": 5, "base_seconds": 8, "max_seconds": 200},
    }
    hot = Policy.from_config(config, mode="hot")
    sweep = Policy.from_config(config, mode="sweep")
    assert hot.rate_limit_rps == 0.25
    assert sweep.rate_limit_rps == 0.1        # a full pass is slower on purpose
    assert hot.user_agent_mode == "honest"
    assert hot.retry_max_attempts == 5
    assert hot.jitter_max_factor == 4.0


def test_accept_encoding_matches_what_can_be_decoded() -> None:
    """Claiming brotli without being able to decompress it makes every content
    check downstream fail, which is how a challenge page was misread once."""
    from worker.fetch.client import accept_encoding, brotli_available

    assert accept_encoding(impersonating=True) == "gzip, deflate, br"
    expected = "gzip, deflate, br" if brotli_available() else "gzip, deflate"
    assert accept_encoding(impersonating=False) == expected


def test_honest_request_does_not_claim_undecodable_encoding() -> None:
    from worker.fetch.client import brotli_available

    transport = FakeTransport()
    client, _ = make_client(transport, user_agent_mode="honest")
    client.get("https://example.com/p")
    page = next(c for c in transport.calls if c.url.endswith("/p"))
    if not brotli_available():
        assert "br" not in page.headers["Accept-Encoding"]


def test_policy_defaults_are_conservative() -> None:
    policy = Policy.from_config({})
    assert policy.respect_robots is True
    assert policy.rate_limit_rps <= 0.5
    assert policy.max_requests <= 500
