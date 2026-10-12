"""The transport's routing: direct where it works, the proxy where it must.

The three portals do not agree about the server. Measured from the server
itself on 27 September 2026: Rightmove serves it, Zoopla answers 403 under
every fingerprint, OpenRent answers 405. From a home connection all three
serve the identical requests, so what the other two object to is the address.

That makes the proxy an escalation rather than a setting, and these tests pin
the behaviour that follows from it — none of which can be reproduced from a
developer's own connection, because from here nothing is blocked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
from curl_cffi import CurlError

from worker.sources.fetch import (
    BLOCKED,
    JITTER,
    ROTATE,
    STICKY_PORTS,
    Fetcher,
    Refused,
    Reply,
    exit_ports,
    host_of,
)

PROXY = "http://user:pass@gw.dataimpulse.com:823"


@dataclass
class Attempt:
    target: str
    through_proxy: bool


class Fake(Fetcher):
    """A Fetcher whose single request is scripted rather than performed."""

    def __init__(self, answers: dict[bool, int], **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        # status to answer with, per route: True = through the proxy.
        self.answers = answers
        self.tried: list[Attempt] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, through_proxy: bool
    ) -> Reply:
        self.tried.append(Attempt(target, through_proxy))
        status = self.answers.get(through_proxy, 200)
        self.requests += 1
        if through_proxy:
            self.proxied += 1
        return Reply(status=status, body="ok", wire=1000, impersonated=target)


ZOOPLA = "https://www.zoopla.co.uk/to-rent/property/e14/"
OPENRENT = "https://www.openrent.co.uk/properties-to-rent/e14"
IMAGE = "https://media.rightmove.co.uk/dir/crop/x_max_476x317.jpeg"


def test_a_portal_that_serves_us_never_touches_the_proxy() -> None:
    # Rightmove is the biggest share of the traffic and it serves the server
    # directly. Routing everything through the proxy to satisfy the other two
    # would put that share on a metered connection for no reason.
    get = Fake({False: 200}, proxy=PROXY)
    reply = get.get(ZOOPLA)

    assert reply.status == 200
    assert get.proxied == 0
    assert [one.through_proxy for one in get.tried] == [False]


@pytest.mark.parametrize("status", [403, 405, 429, 401])
def test_a_refusal_escalates_to_the_proxy(status: int) -> None:
    # 403 is Zoopla's answer, 405 is OpenRent's. Neither is a statement about
    # the request; both are statements about where it came from.
    get = Fake({False: status, True: 200}, proxy=PROXY)
    reply = get.get(ZOOPLA)

    assert reply.status == 200
    assert get.proxied == 1
    assert get.tried[-1].through_proxy is True
    assert host_of(ZOOPLA) in get.blocked


def test_a_404_is_an_answer_and_is_never_retried_through_paid_traffic() -> None:
    # Asking again from somewhere else would spend money to be told the same
    # thing twice.
    get = Fake({False: 404, True: 200}, proxy=PROXY)
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert get.proxied == 0
    assert get.blocked == set()


def test_a_server_error_is_theirs_and_is_not_escalated() -> None:
    get = Fake({False: 500, True: 200}, proxy=PROXY)
    with pytest.raises(Refused):
        get.get(ZOOPLA)
    assert get.proxied == 0


def test_the_wasted_probe_is_paid_once_per_host_per_run() -> None:
    # A refusal is remembered, so only the first district of a run asks
    # directly and finds out. Twenty districts would otherwise mean twenty
    # refused requests before twenty successful ones.
    get = Fake({False: 403, True: 200}, proxy=PROXY)
    get.get(ZOOPLA)
    direct_first_time = [one for one in get.tried if not one.through_proxy]
    assert direct_first_time, "the first request should try this address"

    get.tried.clear()
    get.get(ZOOPLA + "?pn=2")
    assert [one.through_proxy for one in get.tried] == [True]


def test_the_memory_is_per_host_and_not_shared() -> None:
    # OpenRent refusing us says nothing about Zoopla, and a run reads both.
    get = Fake({False: 403, True: 200}, proxy=PROXY)
    get.get(ZOOPLA)
    get.tried.clear()
    get.get(OPENRENT)

    # The second host probes directly on its own account.
    assert next(one.through_proxy for one in get.tried) is False


def test_it_is_forgotten_when_the_run_ends() -> None:
    # Deliberately per Fetcher and no longer. A portal that stops blocking us
    # goes back to being free by itself on the next run, with nothing to
    # reconfigure — and a config file that has to be edited when a block lifts
    # is a config file that will be wrong.
    first = Fake({False: 403, True: 200}, proxy=PROXY)
    first.get(ZOOPLA)
    assert first.blocked

    later = Fake({False: 200}, proxy=PROXY)
    assert later.blocked == set()
    later.get(ZOOPLA)
    assert later.proxied == 0


def test_a_cdn_picture_is_never_proxied_however_blocked_we_are() -> None:
    # The pictures are on a CDN that serves anybody, and sending them through
    # paid residential traffic is the most expensive way to fetch the cheapest
    # thing. In practice nothing here downloads them at all — the url is
    # stored and Telegram fetches it — but the rule holds either way.
    get = Fake({False: 403, True: 200}, proxy=PROXY,
               direct_hosts=("media.rightmove.co.uk",))
    assert get.never_proxy(IMAGE) is True
    with pytest.raises(Refused):
        get.get(IMAGE)
    assert get.proxied == 0


def test_with_no_proxy_a_refusal_stays_a_refusal() -> None:
    # Which is what the server is doing today, and why the error in the log
    # said what it said.
    get = Fake({False: 403}, proxy=None)
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert "403" in str(raised.value)
    assert get.proxied == 0
    # Recorded even though there was nowhere to escalate to. This is the only
    # evidence that the portal objects to the address rather than to the
    # request, and it is what lets the run say "set SCRAPE_PROXY" instead of
    # only "something went wrong".
    assert get.blocked == {"www.zoopla.co.uk"}


def test_a_fingerprint_rule_is_answered_by_rotation_before_the_proxy() -> None:
    # 403 can be either a fingerprint rule or an address rule, and rotating is
    # free where the proxy is not. So every fingerprint is tried on this
    # address first, and only then is the address the suspect.
    get = Fake({False: 403, True: 200}, proxy=PROXY,
               targets=("chrome124", "safari17_0", "chrome123"))
    get.get(ZOOPLA)

    direct = [one.target for one in get.tried if not one.through_proxy]
    assert direct == ["chrome124", "safari17_0", "chrome123"]
    assert sum(1 for one in get.tried if one.through_proxy) == 1


def test_the_two_status_lists_say_what_they_mean() -> None:
    # 405 escalates but never rotates: it is not a fingerprint answer.
    assert 405 in BLOCKED and 405 not in ROTATE
    assert 404 not in BLOCKED and 404 not in ROTATE
    assert all(one in BLOCKED for one in ROTATE)


def test_the_hostname_is_read_without_its_port_or_path() -> None:
    assert host_of("https://media.rightmove.co.uk:443/dir/a.jpg") == "media.rightmove.co.uk"
    assert host_of("http://WWW.Zoopla.co.uk/x") == "www.zoopla.co.uk"


# ── rotating the exit address ──────────────────────────────────────────────
#
# Measured on 9 October 2026 from `job_events`: `zoopla_london` was refused on
# a run and served on the next one five minutes later, over and over, so a
# working exit existed each time and the refused run had no way to ask for it.
# These pin the answer to that. See the module note in worker.sources.fetch.

STICKY = "http://user:pass@gw.dataimpulse.com:10000"


class Rotating(Fetcher):
    """A Fetcher whose exits refuse until the n-th one, then serve.

    Scripted on the proxy url rather than on the call count, because what is
    being tested is that a *different address* is what gets asked — not merely
    that something was asked again.
    """

    def __init__(self, serves_on: int, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.serves_on = serves_on
        self.exits_tried: list[str] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, through_proxy: bool
    ) -> Reply:
        self.requests += 1
        if not through_proxy:
            return Reply(status=403, body="", wire=1, impersonated=target)
        self.proxied += 1
        self.exits_tried.append(self._proxy_now())
        status = 200 if len(self.exits_tried) >= self.serves_on else 403
        return Reply(status=status, body="ok", wire=1, impersonated=target)


def test_a_refused_exit_is_retried_on_a_different_one() -> None:
    get = Rotating(2, proxy=STICKY)
    assert get.get(ZOOPLA).status == 200

    # Two exits, and the second really was a different address: on a sticky
    # setup that means a different port, because reconnecting on the same port
    # returns the same session and therefore the same address.
    assert len(get.exits_tried) == 2
    assert len(set(get.exits_tried)) == 2
    assert get.rotations == 1


def test_the_exit_that_worked_is_kept_for_the_rest_of_the_run() -> None:
    # A sweep of twenty districts must not pay a handshake and a fresh gamble
    # per district once it has found an exit the portal serves.
    get = Rotating(2, proxy=STICKY)
    get.get(ZOOPLA)
    found = get.exits_tried[-1]
    get.get(ZOOPLA + "?pn=2")

    assert get.exits_tried[-1] == found
    assert get.rotations == 1


def test_the_proxy_route_spends_its_budget_on_addresses_not_fingerprints() -> None:
    # The inversion that this change is: the old ladder tried four
    # fingerprints against one address, which spent three paid 403s learning
    # what the first one had already said.
    get = Rotating(99, proxy=STICKY, exits=3,
                   targets=("chrome124", "safari17_0", "chrome123", "edge101"))
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert len(get.exits_tried) == 3
    assert len(set(get.exits_tried)) == 3
    # And the message names what was actually varied, because that sentence is
    # all a later look at `job_events` has to go on.
    assert "3 exit addresses" in str(raised.value)


def test_a_rotation_costs_nothing_when_the_first_exit_serves() -> None:
    get = Rotating(1, proxy=STICKY)
    get.get(ZOOPLA)

    assert get.rotations == 0
    assert len(get.exits_tried) == 1


def test_the_rotating_port_is_left_on_its_own_port() -> None:
    # 823 assigns an address per connection, so dropping the handle is the
    # whole mechanism there and moving the port would only point at one
    # nobody is listening on.
    get = Rotating(2, proxy=PROXY)
    get.get(ZOOPLA)

    assert get.exits_tried == [PROXY, PROXY]
    assert get.rotations == 1


def test_only_a_sticky_port_gets_the_range() -> None:
    assert exit_ports(STICKY) == tuple(STICKY_PORTS)
    assert exit_ports(PROXY) == (823,)
    assert exit_ports("http://user:pass@gw.example") == ()
    assert exit_ports(None) == ()


def test_two_readers_through_one_proxy_do_not_share_one_exit() -> None:
    # Three readers scrape through this proxy and two of them are Zoopla.
    # Started from the one configured port they would share one sticky session
    # and so one address, and a single burned exit would take all three out at
    # once. The offset is the pid, so this asserts the mechanism rather than
    # the value.
    get = Fetcher(proxy=STICKY)
    assert get._exit == os.getpid() % len(STICKY_PORTS)
    assert get._proxy_now() != STICKY or len(STICKY_PORTS) == 1


def test_a_server_error_on_the_probe_does_not_blame_the_proxy() -> None:
    # A 500 stops on the direct route — it is their fault, not worth paying to
    # hear twice — so the sentence that reaches `job_events` must not name a
    # proxy the request never went near. It used to, by reading the end of the
    # route list rather than the route actually reached.
    get = Fake({False: 500, True: 200}, proxy=PROXY)
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert "this address" in str(raised.value)
    assert "proxy" not in str(raised.value)
    assert get.proxied == 0


def test_every_request_waits_a_different_random_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    # A timer fires on the dot, and a sweep's pages follow each other as fast
    # as a reused handle can ask. Both are shapes no person browsing produces,
    # so the delay is drawn per request rather than once per run — one delay
    # chosen at the start would move the burst and leave its shape alone.
    slept: list[float] = []
    monkeypatch.setattr("worker.sources.fetch.time.sleep", slept.append)
    monkeypatch.setattr("worker.sources.fetch.random.uniform", lambda low, high: high)

    get = Fetcher(jitter=2.0)
    get._pause()
    get._pause()

    assert slept == [2.0, 2.0]


def test_jitter_can_be_turned_off_without_touching_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Zero has to mean zero rather than "unset": the tests above would
    # otherwise each pay a real delay per request, and a reader run by hand
    # from a laptop has nothing to hide from.
    def fail(_: float) -> None:
        raise AssertionError("slept with jitter off")

    monkeypatch.setattr("worker.sources.fetch.time.sleep", fail)
    Fetcher(jitter=0)._pause()


def test_the_jitter_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo must not stop every reader on the box, and zero is meaningful
    # here, so it cannot double as "unset".
    monkeypatch.setenv("SCRAPE_JITTER", "0.25")
    assert Fetcher.from_env().jitter == 0.25

    monkeypatch.setenv("SCRAPE_JITTER", "0")
    assert Fetcher.from_env().jitter == 0

    monkeypatch.setenv("SCRAPE_JITTER", "soon")
    assert Fetcher.from_env().jitter == JITTER

    monkeypatch.delenv("SCRAPE_JITTER")
    assert Fetcher.from_env().jitter == JITTER


# ── when nothing answers at all ────────────────────────────────────────────
#
# Observed on `zoopla_london` on 11 October 2026, three times in one day:
#
#     curl: (60) SSL certificate ... unable to get local issuer certificate
#     curl: (7)  CONNECT tunnel failed, response 502
#     curl: (28) Connection timed out after 15002 milliseconds
#
# Each is one exit address failing, which is the same news as a 403 through
# the proxy. It was not treated as such: `CurlError` is neither `Refused` nor
# `OSError`, so it went past the rotation and past every handler at the call
# sites and ended the run on its first district. These pin the handling.

# Real libcurl messages, kept verbatim — the text is what gets parsed for
# `job_events`, and a paraphrase would not prove anything about the real one.
SAID = {
    60: (
        "Failed to perform, curl: (60) SSL certificate OpenSSL verify result: "
        "unable to get local issuer certificate (20). See "
        "https://curl.se/libcurl/c/libcurl-errors.html first for more details."
    ),
    7: (
        "Failed to perform, curl: (7) CONNECT tunnel failed, response 502. See "
        "https://curl.se/libcurl/c/libcurl-errors.html first for more details."
    ),
    28: (
        "Failed to perform, curl: (28) Connection timed out after 15002 "
        "milliseconds. See "
        "https://curl.se/libcurl/c/libcurl-errors.html first for more details."
    ),
}

YESTERDAY = sorted(SAID)


def broke(code: int) -> CurlError:
    return CurlError(SAID.get(code, f"Failed to perform, curl: ({code}) ."), code)


class Breaking(Fetcher):
    """A Fetcher whose connection fails rather than answering.

    Scripted per route, like `Fake`, and per attempt: `serves_on` is how many
    proxied attempts it takes before one works, so 99 means none ever does.
    """

    def __init__(
        self, code: int, *, direct_breaks: bool = True, serves_on: int = 99, **kw: object
    ) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.code = code
        self.direct_breaks = direct_breaks
        self.serves_on = serves_on
        self.exits_tried: list[str] = []
        self.routes: list[bool] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, through_proxy: bool
    ) -> Reply:
        self.requests += 1
        self.routes.append(through_proxy)
        if not through_proxy:
            if self.direct_breaks:
                raise broke(self.code)
            return Reply(status=403, body="", wire=1, impersonated=target)
        self.proxied += 1
        self.exits_tried.append(self._proxy_now())
        if len(self.exits_tried) < self.serves_on:
            raise broke(self.code)
        return Reply(status=200, body="ok", wire=1, impersonated=target)


@pytest.mark.parametrize("code", YESTERDAY)
def test_a_broken_tunnel_is_retried_on_a_different_exit(code: int) -> None:
    # The whole point. A tunnel that would not open says nothing about the
    # next address, and there was a working one each time: the refusals in the
    # module note alternate run to run five minutes apart.
    get = Breaking(code, direct_breaks=False, serves_on=2, proxy=STICKY)
    assert get.get(ZOOPLA).status == 200

    assert len(get.exits_tried) == 2
    assert len(set(get.exits_tried)) == 2
    assert get.rotations == 1


@pytest.mark.parametrize("code", YESTERDAY)
def test_every_exit_breaking_is_a_refusal_and_not_a_crash(code: int) -> None:
    # `Refused` is what every call site handles: the district is counted,
    # described, left incomplete and read again next run. A `CurlError` here
    # is what killed the stage and took the other nineteen districts with it.
    get = Breaking(code, direct_breaks=False, proxy=STICKY, exits=3)
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert len(set(get.exits_tried)) == 3
    said = str(raised.value)
    # Names the reason and the route, because that sentence is all a later
    # look at `job_events` has to go on — and keeps libcurl's own code in it.
    assert f"curl: ({code})" in said
    assert "from the proxy over 3 exit addresses" in said
    # curl_cffi's boilerplate is not news and is three times the length of
    # what is.
    assert "See https://" not in said
    assert "Failed to perform" not in said


def test_a_broken_connection_from_here_escalates_to_the_proxy() -> None:
    # A portal that null-routes this address looks exactly like bad network
    # from here, and the proxy is the only other place to ask.
    get = Breaking(28, serves_on=1, proxy=PROXY)
    assert get.get(ZOOPLA).status == 200
    assert get.proxied == 1


def test_a_broken_connection_from_here_is_not_remembered_as_a_block() -> None:
    # The difference from a 403. That memory sends every later district
    # straight to paid traffic, and one timeout is not evidence enough to
    # spend money on nineteen of them — a portal that refuses this address
    # refuses it every time, and a flaky moment does not.
    get = Breaking(7, serves_on=1, proxy=PROXY)
    get.get(ZOOPLA)
    assert get.blocked == set()

    get.routes.clear()
    get.direct_breaks = False  # whatever it was, it has stopped
    get.get(ZOOPLA + "?pn=2")
    assert get.routes[0] is False, "the free route must be tried again next time"


def test_a_fingerprint_is_not_rotated_against_a_connection_that_never_opened() -> None:
    # Four handshakes that never got as far as a handshake. The direct route's
    # ladder varies the fingerprint, and a tunnel or a timeout is no evidence
    # about one, so it stops at the first.
    get = Breaking(60, serves_on=99, proxy=PROXY, exits=1)
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    # One direct attempt, one through the proxy. Not four and four.
    assert get.requests == 2


def test_a_broken_connection_with_no_proxy_configured_is_still_a_refusal() -> None:
    get = Breaking(28, proxy=None)
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert "from this address" in str(raised.value)
    assert "exit address" not in str(raised.value)


def test_our_own_bug_is_not_dressed_up_as_the_portal_refusing_us() -> None:
    # A malformed url is ours, and a bug that fails loudly on the first run is
    # worth more than one folded away into "the portal refused us". 3 is
    # URL_MALFORMAT, and it is deliberately not in BROKEN.
    get = Breaking(3, proxy=PROXY)
    with pytest.raises(CurlError):
        get.get(ZOOPLA)


def test_three_dead_exits_are_still_three_requests_somebody_paid_for() -> None:
    # Driven through the real `_once` against a port nothing listens on, so
    # this is libcurl's own error and libcurl's own counters. A refused tunnel
    # and a timed-out handshake both cost the proxy's traffic, and the stage
    # reports `requests` as what the run cost: reading zero after three
    # attempts is why yesterday's failures looked like no attempt at all.
    get = Fetcher(jitter=0, exits=3, timeout=2.0)
    with pytest.raises(Refused) as raised:
        # 127.0.0.1:1 refuses immediately, so this is fast and offline.
        get.get("http://127.0.0.1:1/to-rent/e14")

    assert get.requests == 1, "one fingerprint, no proxy to escalate to"
    assert "curl: (7)" in str(raised.value)
    get.close()


def test_a_dead_handle_is_not_kept_for_the_next_request() -> None:
    # A handle that failed mid-transfer may be holding a connection in an
    # unknown state, and the next request on it would fail for a reason that
    # has nothing to do with the next request.
    get = Fetcher(jitter=0, timeout=2.0)
    with pytest.raises(Refused):
        get.get("http://127.0.0.1:1/to-rent/e14")

    assert get._handles == {}
    get.close()


# ── the same, for the lookup `openrent` makes hundreds of times a run ──────


class BreakingHead(Fetcher):
    """A Fetcher whose HEAD fails to connect, scripted per route."""

    def __init__(self, code: int, *, breaks_through_proxy: bool, **kw: object) -> None:
        super().__init__(**kw)  # type: ignore[arg-type]
        self.code = code
        self.breaks_through_proxy = breaks_through_proxy
        self.routes: list[bool] = []

    def _head_once(  # type: ignore[override]
        self, url: str, *, through_proxy: bool
    ) -> tuple[int, str | None]:
        self.routes.append(through_proxy)
        if through_proxy and not self.breaks_through_proxy:
            return 301, "/property-to-rent/london/2-bed-flat-e14/123"
        raise broke(self.code)


SHORT = "https://www.openrent.co.uk/1234567"


def test_a_lookup_whose_connection_broke_asks_the_proxy() -> None:
    # `openrent` stored 134 listings in a fortnight and announced none,
    # because its lookups never escalated. A broken connection must escalate
    # for the same reason a 405 does: it is the address that is in question.
    get = BreakingHead(60, breaks_through_proxy=False, proxy=PROXY)
    status, where = get.head(SHORT)

    assert (status, get.routes) == (301, [False, True])
    assert where is not None and where.endswith("/123")


def test_a_lookup_broken_on_both_routes_loses_the_lookup_and_not_the_run() -> None:
    # `Refused` is what the caller handles: `slug_unreachable`, the district
    # left incomplete, the id asked about again next run.
    get = BreakingHead(28, breaks_through_proxy=True, proxy=PROXY)
    with pytest.raises(Refused) as raised:
        get.head(SHORT)

    assert get.routes == [False, True]
    assert "curl: (28)" in str(raised.value)
    assert "from the proxy" in str(raised.value)


def test_an_exit_that_broke_a_lookup_is_not_used_for_the_next_one() -> None:
    # One bad exit would otherwise break every remaining lookup in the run,
    # which is a whole slug budget spent on nothing. The rotation is free: the
    # handle holding that dead tunnel has already been dropped.
    get = BreakingHead(7, breaks_through_proxy=True, proxy=STICKY)
    first = get._proxy_now()
    with pytest.raises(Refused):
        get.head(SHORT)

    assert get._proxy_now() != first
    assert get.rotations >= 1


def test_a_lookup_with_no_proxy_configured_is_refused_once_and_not_twice() -> None:
    get = BreakingHead(7, breaks_through_proxy=True, proxy=None)
    with pytest.raises(Refused) as raised:
        get.head(SHORT)

    assert get.routes == [False]
    assert "from this address" in str(raised.value)
