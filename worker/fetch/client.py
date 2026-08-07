"""Polite HTTP client.

The aim is to be a correct client that is not mistaken for an attack, which is a
different goal from being undetectable. It is achieved by pacing, by matching a
real client's TLS and header fingerprint, and by respecting `robots.txt` — all of
which align with the site's own interest.

Failures are raised rather than returned. A refusal, a challenge, or an exhausted
budget must stop the run, and an exception cannot be accidentally ignored the way
a status code can. Continuing to request from a host that has just answered 403 is
the quickest route from a temporary limit to a lasting block.

Every source of non-determinism — time, sleeping, jitter, the transport itself —
is injected, so the pacing and back-off logic is tested without a network or a
wall clock.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from worker.fetch.robots import RobotsTxt

HONEST_UA = "LondonRentAlerts/0.1 (+https://github.com/alexremnev/new-home-finder)"
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# A page that asks a human to prove they are one. Distinguished from a plain
# refusal because the responses differ: a refusal may be a misclassification worth
# correcting, whereas answering a challenge automatically is out of scope.
CHALLENGE_MARKERS = (
    b"captcha",
    b"human verification",
    b"verify that you're not a robot",
    b"verify you are human",
    b"just a moment",
    b"enable javascript and cookies",
    b"are you a robot",
    b"press and hold",
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 408})


class FetchError(RuntimeError):
    """Base class. Every subclass means the run should stop for this source."""


class Disallowed(FetchError):
    """robots.txt forbids this path for us."""


class Challenged(FetchError):
    """The host asked for human verification. Not something to automate past."""


class Blocked(FetchError):
    """The host refused us outright, after any permitted correction."""


class BudgetExhausted(FetchError):
    """The run hit its request or time ceiling. A bug must not become a flood."""


class FetchFailed(FetchError):
    """Retries were exhausted on a transient failure."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False
    elapsed_ms: int = 0

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class Transport(Protocol):
    def get(
        self, url: str, *, headers: dict[str, str], impersonate: str | None, timeout: float
    ) -> Response: ...


@dataclass
class Policy:
    """Per-source behaviour, read from `sources.config`."""

    user_agent_mode: str = "auto"  # honest | chrome | auto
    impersonate: str | None = "chrome124"
    rate_limit_rps: float = 0.3
    concurrency: int = 1
    shuffle_urls: bool = True
    respect_robots: bool = True
    conditional_requests: bool = True
    jitter_min_factor: float = 0.4
    jitter_max_factor: float = 3.0
    retry_max_attempts: int = 3
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 120.0
    timeout_seconds: float = 30.0
    max_requests: int = 200
    max_runtime_s: float = 480.0

    @classmethod
    def from_config(cls, config: dict[str, Any], *, mode: str = "hot") -> Policy:
        jitter = config.get("jitter") or {}
        retry = config.get("retry") or {}
        rps_key = "sweep_rate_limit_rps" if mode == "sweep" else "rate_limit_rps"
        return cls(
            user_agent_mode=config.get("user_agent_mode", "auto"),
            impersonate=config.get("impersonate", "chrome124"),
            rate_limit_rps=float(config.get(rps_key) or config.get("rate_limit_rps") or 0.3),
            concurrency=int(config.get("concurrency", 1)),
            shuffle_urls=bool(config.get("shuffle_urls", True)),
            respect_robots=bool(config.get("respect_robots", True)),
            conditional_requests=bool(config.get("conditional_requests", True)),
            jitter_min_factor=float(jitter.get("min_factor", 0.4)),
            jitter_max_factor=float(jitter.get("max_factor", 3.0)),
            retry_max_attempts=int(retry.get("max_attempts", 3)),
            retry_base_seconds=float(retry.get("base_seconds", 5)),
            retry_max_seconds=float(retry.get("max_seconds", 120)),
        )


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    not_modified: int = 0
    skipped_by_robots: int = 0
    ua_escalations: int = 0
    slept_seconds: float = 0.0


class PoliteClient:
    def __init__(
        self,
        transport: Transport,
        *,
        policy: Policy | None = None,
        validators: dict[str, tuple[str | None, str | None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.transport = transport
        self.policy = policy or Policy()
        # ETag and Last-Modified per URL, so an unchanged page costs almost nothing.
        self.validators = validators if validators is not None else {}
        self.clock = clock
        self.sleeper = sleeper
        self.rng = rng or random.Random()

        self.stats = Stats()
        self._robots: dict[str, RobotsTxt] = {}
        self._crawl_delay: dict[str, float | None] = {}
        self._last_request_at: dict[str, float] = {}
        self._started_at = clock()
        self._escalated = False
        self._ua_mode = self.policy.user_agent_mode

    # ── public ────────────────────────────────────────────────────────────

    def get(self, url: str, *, extra_headers: dict[str, str] | None = None) -> Response:
        self._check_budget()
        if self.policy.respect_robots and not self._allowed(url):
            self.stats.skipped_by_robots += 1
            raise Disallowed(f"robots.txt disallows {url}")
        return self._request_with_retries(url, extra_headers or {})

    def order(self, urls: list[str]) -> list[str]:
        """Shuffle a work list.

        Walking identifiers or pages in order is one of the most recognisable
        signatures of an automated client, and the order carries no meaning here.
        """
        if not self.policy.shuffle_urls:
            return list(urls)
        out = list(urls)
        self.rng.shuffle(out)
        return out

    # ── robots ────────────────────────────────────────────────────────────

    def _allowed(self, url: str) -> bool:
        host = _origin(url)
        if host not in self._robots:
            self._load_robots(host)
        return self._robots[host].can_fetch(HONEST_UA, url)

    def _load_robots(self, origin: str) -> None:
        """Fetch and parse robots.txt once per host.

        Read with our real identity and with no impersonation at all — an honest
        User-Agent over a masked TLS handshake is not honest, and a host can serve
        a different file to each. What a header claims does not change what we
        are, and the permission question is about us. An unavailable file is
        treated as no rules, per RFC 9309, but that is recorded rather than
        assumed to be a licence.
        """
        rules = RobotsTxt()
        try:
            response = self._raw_request(
                f"{origin}/robots.txt", {}, ua=HONEST_UA, impersonate=None
            )
            if response.status == 200 and response.body:
                rules = RobotsTxt.parse(response.body.decode("utf-8", errors="replace"))
        except FetchError:
            pass
        self._robots[origin] = rules
        self._crawl_delay[origin] = rules.crawl_delay(HONEST_UA)

    # ── requesting ────────────────────────────────────────────────────────

    def _request_with_retries(self, url: str, extra: dict[str, str]) -> Response:
        attempt = 0
        while True:
            attempt += 1
            response = self._raw_request(url, extra)

            if _is_challenge(response):
                raise Challenged(
                    f"{url} returned an interactive challenge (status {response.status}); "
                    "answering it is out of scope"
                )

            if response.status == 403:
                if self._can_escalate():
                    self._escalate()
                    continue
                raise Blocked(f"{url} refused with 403")

            if response.status in RETRY_STATUSES:
                if attempt > self.policy.retry_max_attempts:
                    raise FetchFailed(
                        f"{url} still failing with {response.status} after {attempt - 1} retries"
                    )
                self.stats.retries += 1
                self._sleep(self._backoff(attempt, response))
                continue

            if response.status == 304:
                self.stats.not_modified += 1
                response.from_cache = True
                return response

            if response.status == 200:
                self._remember_validators(url, response)
            return response

    _UNSET = object()

    def _raw_request(
        self,
        url: str,
        extra: dict[str, str],
        *,
        ua: str | None = None,
        impersonate: str | None | object = _UNSET,
    ) -> Response:
        self._pace(url)
        profile = (
            (self.policy.impersonate if self._ua_mode != "honest" else None)
            if impersonate is self._UNSET
            else impersonate
        )
        headers = self._headers(url, extra, ua, impersonating=profile is not None)
        started = self.clock()
        try:
            response = self.transport.get(
                url,
                headers=headers,
                impersonate=profile,  # type: ignore[arg-type]
                timeout=self.policy.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - transport failures are retried by caller
            raise FetchFailed(f"{url}: {type(exc).__name__}: {exc}") from exc
        self.stats.requests += 1
        response.elapsed_ms = int((self.clock() - started) * 1000)
        self._last_request_at[_origin(url)] = self.clock()
        return response

    def _headers(
        self, url: str, extra: dict[str, str], ua: str | None, *, impersonating: bool
    ) -> dict[str, str]:
        headers = {
            "User-Agent": ua or self._user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept-Encoding": accept_encoding(impersonating=impersonating),
            "Upgrade-Insecure-Requests": "1",
        }
        if self.policy.conditional_requests:
            etag, modified = self.validators.get(url, (None, None))
            if etag:
                headers["If-None-Match"] = etag
            if modified:
                headers["If-Modified-Since"] = modified
        headers.update(extra)
        return headers

    def _user_agent(self) -> str:
        return CHROME_UA if self._ua_mode == "chrome" else HONEST_UA

    def _remember_validators(self, url: str, response: Response) -> None:
        etag = response.header("etag")
        modified = response.header("last-modified")
        if etag or modified:
            self.validators[url] = (etag, modified)

    # ── user-agent escalation ─────────────────────────────────────────────

    def _can_escalate(self) -> bool:
        return self.policy.user_agent_mode == "auto" and not self._escalated

    def _escalate(self) -> None:
        """Retry once as a browser, then stop.

        Most filters reject an unknown User-Agent by default rather than refusing
        deliberately, so one correction on a path robots.txt permits is fair.
        Cycling values until something works is not: that is defeating a decision
        rather than fixing a misreading, so there is exactly one attempt.
        """
        self._escalated = True
        self._ua_mode = "chrome"
        self.stats.ua_escalations += 1

    # ── pacing ────────────────────────────────────────────────────────────

    def _pace(self, url: str) -> None:
        origin = _origin(url)
        target = self._interval(origin)
        last = self._last_request_at.get(origin)
        wait = target if last is None else max(0.0, target - (self.clock() - last))
        if last is None:
            # Nothing owed on the first request to a host.
            return
        self._sleep(wait)

    def _interval(self, origin: str) -> float:
        rate = max(self.policy.rate_limit_rps, 0.01)
        base = 1.0 / rate
        # Exponential rather than uniform: it matches the shape of real traffic,
        # where a fixed cadence does not occur.
        delay = self.rng.expovariate(1.0 / base)
        low, high = self.policy.jitter_min_factor * base, self.policy.jitter_max_factor * base
        delay = min(max(delay, low), high)
        crawl_delay = self._crawl_delay.get(origin)
        return max(delay, crawl_delay) if crawl_delay else delay

    def _backoff(self, attempt: int, response: Response) -> float:
        retry_after = response.header("retry-after")
        if retry_after:
            try:
                # The site said how long to wait; that instruction wins.
                return min(float(retry_after), self.policy.retry_max_seconds)
            except ValueError:
                pass
        base = self.policy.retry_base_seconds * (2 ** (attempt - 1))
        return min(base + self.rng.uniform(0, base * 0.25), self.policy.retry_max_seconds)

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self.stats.slept_seconds += seconds
        self.sleeper(seconds)

    # ── budget ────────────────────────────────────────────────────────────

    def _check_budget(self) -> None:
        if self.stats.requests >= self.policy.max_requests:
            raise BudgetExhausted(
                f"request ceiling reached ({self.policy.max_requests}); "
                "a pagination bug must not become a flood"
            )
        if self.clock() - self._started_at >= self.policy.max_runtime_s:
            raise BudgetExhausted(f"time ceiling reached ({self.policy.max_runtime_s}s)")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _is_challenge(response: Response) -> bool:
    if response.header("cf-mitigated") == "challenge":
        return True
    head = response.body[:8000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


# ── real transport ────────────────────────────────────────────────────────


class HttpTransport:
    """The production transport.

    One session per run, so cookies persist: a client that arrives as a stranger
    on every request looks less like a browser than one that does not. TLS and
    header-order fingerprinting come from `curl_cffi`'s impersonation, since a
    mismatch between a stated User-Agent and the TLS handshake is the clearest
    signal of automation there is.
    """

    def __init__(self) -> None:
        self._sessions: dict[str | None, Any] = {}

    def get(
        self, url: str, *, headers: dict[str, str], impersonate: str | None, timeout: float
    ) -> Response:
        session = self._session(impersonate)
        if impersonate:
            raw = session.get(url, headers=headers, impersonate=impersonate, timeout=timeout)
        else:
            raw = session.get(url, headers=headers, timeout=timeout)
        return Response(
            status=raw.status_code,
            body=raw.content or b"",
            headers={k.lower(): v for k, v in raw.headers.items()},
        )

    def _session(self, impersonate: str | None) -> Any:
        if impersonate not in self._sessions:
            if impersonate:
                from curl_cffi import requests as creq

                self._sessions[impersonate] = creq.Session()
            else:
                import requests

                self._sessions[impersonate] = requests.Session()
        return self._sessions[impersonate]


def brotli_available() -> bool:
    try:
        import brotli  # noqa: F401, PLC0415
    except ImportError:
        try:
            import brotlicffi  # noqa: F401, PLC0415
        except ImportError:
            return False
    return True


def accept_encoding(*, impersonating: bool) -> str:
    """Advertise only what this request can actually decode.

    `curl_cffi` decodes brotli itself; `requests` needs an optional package.
    Claiming brotli without it yields an undecoded body, and every content check
    downstream then fails — a challenge page reads as binary noise instead of a
    challenge, which is exactly how one was misread once already.
    """
    if impersonating or brotli_available():
        return "gzip, deflate, br"
    return "gzip, deflate"
