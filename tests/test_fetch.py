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

import pathlib
import time
from dataclasses import dataclass

import pytest
from curl_cffi import CurlError

from worker.sources import session
from worker.sources.fetch import (
    BACKOFF_CAP,
    BLOCKED,
    DIRECT,
    FALLBACK,
    FALLBACK_ATTEMPTS,
    JITTER,
    KEPT_REFUSALS,
    PRIMARY,
    RETRY_AFTER_CAP,
    ROTATE,
    STICKY_PORTS,
    Fetcher,
    Refused,
    Reply,
    _evidence,
    exit_ports,
    host_of,
)

PROXY = "http://user:pass@gw.dataimpulse.com:823"


@dataclass
class Attempt:
    target: str
    #: Which route carried it: `DIRECT`, `PRIMARY` or `FALLBACK`.
    via: str

    @property
    def through_proxy(self) -> bool:
        """Whether it was paid for at all, which is what most tests ask."""

        return self.via != DIRECT


class Fake(Fetcher):
    """A Fetcher whose single request is scripted rather than performed."""

    def __init__(self, answers: dict[object, int], **kw: object) -> None:
        kw.setdefault("backoff", 0)
        super().__init__(**kw)  # type: ignore[arg-type]
        #: Status to answer with. Keyed either by route name — `PRIMARY`,
        #: `FALLBACK` — for a test that cares which provider answered, or by
        #: the older "was it paid for" boolean, which is what most of these
        #: ask and what keeps them readable.
        self.answers = answers
        self.tried: list[Attempt] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, via: str
    ) -> Reply:
        self.tried.append(Attempt(target, via))
        status = self.answers.get(via, self.answers.get(via != DIRECT, 200))
        self._spent(1000, via)
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


def test_a_block_outlives_the_run_that_learned_it() -> None:
    # It used to be forgotten at the end of the run, on the reasoning that a
    # portal which stops blocking us should go back to being free by itself.
    # The reasoning survives — see the test below — but the price did not: the
    # probe that re-learns it is a request whose answer has been the same since
    # September, so every run paid one 403 to be told what the last one knew.
    # On a five-minute timer that is twelve wasted requests an hour, from the
    # very address the portal is already scoring. See `session.BLOCK_FOR`.
    first = Fake({False: 403, True: 200}, proxy=PROXY)
    first.get(ZOOPLA)
    assert first.blocked == {"www.zoopla.co.uk"}
    first.close()

    later = Fake({False: 200, True: 200}, proxy=PROXY)
    assert later.blocked == {"www.zoopla.co.uk"}, "a separate Fetcher, as a run is"
    later.get(ZOOPLA)
    # Straight to the proxy, and the saving is counted rather than implied.
    assert [one.through_proxy for one in later.tried] == [True]
    assert later.probes_skipped == 1


def test_a_block_that_has_expired_is_probed_directly_again() -> None:
    # The half of the old behaviour that had to be kept. Nothing has to be
    # reconfigured when a portal stops refusing us; it only takes longer to
    # notice than it did, and `BLOCK_FOR` is how long.
    first = Fake({False: 403, True: 200}, proxy=PROXY)
    first.get(ZOOPLA)
    first.close()

    aged = session.Session.load("default")
    aged.blocked = {"www.zoopla.co.uk": time.time() - 1}
    aged.dirty = True
    aged.save()

    later = Fake({False: 200}, proxy=PROXY)
    assert later.blocked == set()
    later.get(ZOOPLA)
    assert later.proxied == 0


def test_a_block_is_never_remembered_for_a_host_pinned_to_the_direct_route() -> None:
    # SpareRoom is pinned in code, so there is nowhere to escalate it to.
    # Remembering its refusal would not save a request — it would skip the only
    # route that can ever read it, which is a reader that silently stops.
    get = Fake({False: 403}, proxy=PROXY, direct_hosts=("spareroom.co.uk",))
    with pytest.raises(Refused):
        get.get("https://www.spareroom.co.uk/flatshare/?search_id=1")
    get.close()

    assert session.Session.load("default").blocked == {}


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


def test_the_free_route_gathers_evidence_rather_than_exhausting_the_list() -> None:
    # 403 can be either a fingerprint rule or an address rule, and rotating is
    # free where the proxy is not — so the cheap suspect still goes first. What
    # changed is how much of the budget it may have. Two attempts can tell the
    # two rules apart, because a rule that catches chrome150 will not catch
    # firefox147; the third and fourth are three more requests from an address
    # that has already said no twice, and the budget they spend is the budget
    # the paid route needs to actually get the page. See DIRECT_PROBES.
    get = Fake({False: 403, True: 200}, proxy=PROXY,
               targets=("chrome150", "firefox147", "chrome146"))
    get.get(ZOOPLA)

    direct = [one.target for one in get.tried if not one.through_proxy]
    assert direct == ["chrome150", "firefox147"]
    assert sum(1 for one in get.tried if one.through_proxy) == 1


def test_one_url_costs_at_most_the_whole_budget_and_not_two_of_them() -> None:
    # The ceiling nobody could read off the old constants. `EXITS = 3` named
    # the paid ladder and `_order()` named the free one, so a page that failed
    # everywhere cost four plus three — seven requests, which is not what three
    # looks like. Now the number is the number.
    get = Fake({False: 403, True: 403}, proxy=PROXY, attempts=5)
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert len(get.tried) == 5
    assert [one.through_proxy for one in get.tried] == [False, False, True, True, True]


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
        kw.setdefault("backoff", 0)
        super().__init__(**kw)  # type: ignore[arg-type]
        self.serves_on = serves_on
        self.exits_tried: list[str] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, via: str
    ) -> Reply:
        self._spent(1, via)
        if via == DIRECT:
            return Reply(status=403, body="", wire=1, impersonated=target)
        self.exits_tried.append(self._proxy_now(via))
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
    get = Rotating(99, proxy=STICKY, attempts=5,
                   targets=("chrome150", "firefox147", "chrome146", "safari2601"))
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
    # Three readers scrape through this proxy. Started from the one configured
    # port they would share one sticky session and so one address, and a single
    # burned exit would take all three out at once.
    #
    # This used to be done by offsetting into the range by pid, which separated
    # the readers and — because a oneshot unit's pid is arbitrary — separated
    # every *run* from the one before it as well. That second half was the bug:
    # see `test_the_exit_that_worked_is_kept_for_the_next_run`. The name is what
    # separates them now, so what is asserted is two names, not two pids.
    one = Fetcher(proxy=STICKY, session="zoopla")
    two = Fetcher(proxy=STICKY, session="openrent")

    assert one.exit_port != two.exit_port
    assert one._proxy_now(PRIMARY) != two._proxy_now(PRIMARY)
    assert one._proxy_now(PRIMARY) != STICKY or len(STICKY_PORTS) == 1


def test_the_exit_that_worked_is_kept_for_the_next_run() -> None:
    # The point of all of it. A run is a process, so "the rest of the run" was
    # as far as the old stickiness reached: five minutes later the next run
    # asked Zoopla from a residential address it had never used, with no
    # cookies, which is the worst thing to present to a bot score. Measured
    # 12 October 2026: a cold request gets four Set-Cookie headers, a warm one
    # gets a refreshed `__cf_bm` and nothing else.
    first = Rotating(2, proxy=STICKY, session="zoopla")
    first.get(ZOOPLA)
    served = first.exit_port
    first.close()

    # A separate Fetcher under the same name, which is what the next run is.
    second = Rotating(1, proxy=STICKY, session="zoopla")
    assert second.exit_port == served
    second.get(ZOOPLA)
    assert second.rotations == 0, "nothing to rotate away from: it still serves"


def test_a_burned_exit_is_not_offered_again_next_run() -> None:
    # The other half: the old rotation advanced an index, so on a 501-port
    # range it could come back round to an address it had already been refused
    # by. An hour on the cooldown list is what stops that. See session.COOLDOWN.
    first = Rotating(99, proxy=STICKY, session="zoopla", attempts=4)
    with pytest.raises(Refused):
        first.get(ZOOPLA)
    burned = {int(one.rpartition(":")[2]) for one in first.exits_tried}
    first.close()

    # Four in the budget, two of which the free route spends on evidence.
    assert len(burned) == 2, "two paid attempts, two addresses"
    later = Rotating(1, proxy=STICKY, session="zoopla")
    assert later.exit_port not in burned


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
        kw.setdefault("backoff", 0)
        super().__init__(**kw)  # type: ignore[arg-type]
        self.code = code
        self.direct_breaks = direct_breaks
        self.serves_on = serves_on
        self.exits_tried: list[str] = []
        self.routes: list[bool] = []
        #: The same sequence named rather than reduced to a boolean, for the
        #: tests that care which of the two providers was reached.
        self.vias: list[str] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, via: str
    ) -> Reply:
        self._spent(1, via)
        self.routes.append(via != DIRECT)
        self.vias.append(via)
        if via == DIRECT:
            if self.direct_breaks:
                raise broke(self.code)
            return Reply(status=403, body="", wire=1, impersonated=target)
        self.exits_tried.append(self._proxy_now(via))
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
    get = Breaking(code, direct_breaks=False, proxy=STICKY, attempts=5)
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
    # Two attempts in the whole budget, so what this pins is the direct
    # ladder stopping dead rather than the paid ladder's length.
    get = Breaking(60, serves_on=99, proxy=PROXY, attempts=2)
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
    get = Fetcher(jitter=0, attempts=3, timeout=2.0)
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
        #: The same sequence named rather than reduced to a boolean, for the
        #: tests that care which of the two providers was reached.
        self.vias: list[str] = []

    def _head_once(  # type: ignore[override]
        self, url: str, *, via: str
    ) -> tuple[int, str | None]:
        self.routes.append(via != DIRECT)
        self.vias.append(via)
        if via != DIRECT and not self.breaks_through_proxy:
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
    first = get._proxy_now(PRIMARY)
    with pytest.raises(Refused):
        get.head(SHORT)

    assert get._proxy_now(PRIMARY) != first
    assert get.rotations >= 1


def test_a_lookup_with_no_proxy_configured_is_refused_once_and_not_twice() -> None:
    get = BreakingHead(7, breaks_through_proxy=True, proxy=None)
    with pytest.raises(Refused) as raised:
        get.head(SHORT)

    assert get.routes == [False]
    assert "from this address" in str(raised.value)


# ── the cookies, the referer and the diagnosis ─────────────────────────────
#
# These are what the five-minute `zoopla_london` timer needed and did not
# have. Measured against the live London search page on 12 October 2026: it is
# served by Cloudflare, it issues `__cf_bm` with an 1800 second expiry, and a
# request presenting a jar gets one Set-Cookie where a cold one gets four. The
# transport discarded every cookie it was ever given, so every run arrived as
# a visitor the site had never seen.


class Served(Fetcher):
    """A Fetcher whose `_once` is real but whose `perform` is not.

    `Fake` cannot be used for any of this: what is under test is the options
    set on the libcurl handle and the headers parsed off it, which is exactly
    the part `Fake` replaces. So the handle is real and only the transfer is
    scripted.
    """

    def __init__(self, status: int = 200, headers: str = "", **kw: object) -> None:
        kw.setdefault("backoff", 0)
        kw.setdefault("jitter", 0)
        super().__init__(**kw)  # type: ignore[arg-type]
        self.status = status
        self.raw = headers
        self.sent: list[dict[str, object]] = []

    def _once(self, url: str, target: str, accept: str, via: str) -> Reply:
        # Build the handle exactly as the real path does, then record what it
        # was told rather than performing it.
        # The handle is built for its side effects and not to be performed on:
        # building it is what opens the cookie jar, which is the part of this
        # path the tests below are about.
        slot, _ = self._handle(target, via=via)
        came_from = self._seen.get(host_of(url), "")
        self.sent.append(
            {"url": url, "slot": slot, "referer": came_from, "target": target}
        )
        self._spent(1, via)
        return Reply(
            status=self.status,
            body="ok",
            wire=1,
            impersonated=target,
            headers={} if 200 <= self.status < 300 else _evidence(self.raw.encode()),
        )


def test_a_cookie_jar_is_opened_per_exit_address_and_survives_the_run(
    isolated_state: pathlib.Path,
) -> None:
    # The file is what outlives the process, and the process is the run. Six
    # consecutive runs fit inside one `__cf_bm`, which is only reachable if
    # something on disk carries it between them.
    get = Served(proxy=STICKY, session="zoopla")
    get.get(ZOOPLA)
    jar = get._memory.jar("direct", get._best or get.targets[0])

    assert jar is not None
    assert jar.parent == isolated_state / "cookies"
    # libcurl writes the jar on handle cleanup, so closing is what persists it.
    get.close()


def test_cookies_can_be_turned_off_without_turning_the_proxy_off() -> None:
    get = Served(proxy=STICKY, session="zoopla", cookies=False)
    get.get(ZOOPLA)
    get.close()

    assert not list((isolated := get._memory).path.parent.glob("cookies/*"))  # type: ignore[union-attr]
    assert isolated is get._memory


def test_one_handle_per_target_and_route_so_no_two_share_a_jar() -> None:
    # A slot is a target and an address. Two handles writing one jar file would
    # mean the last one closed silently discarding the other's cookies.
    get = Served(403, proxy=PROXY, session="zoopla")
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    slots = [str(one["slot"]) for one in get.sent]
    assert any(one.endswith("@direct") for one in slots)
    # The vendor is named as well as the port, because the two pools number
    # their exits independently: `primary-823` and `fallback-823` are two
    # different houses, and a jar shared between them would present a cookie
    # from the wrong one.
    assert any(one.endswith(f"@{PRIMARY}-823") for one in slots)
    # One slot is one jar file. The claim is the mapping: two slots never land
    # on the same path, so the last handle closed cannot discard what another
    # one had been given.
    jars = {
        get._memory.jar(one.split("@")[1], one.split("@")[0]) for one in slots
    }
    assert len(jars) == len(set(slots))
    get.close()


def test_the_second_page_of_a_search_quotes_the_first() -> None:
    # A browser paginating carries the previous page here; we carried nothing
    # at all, on every request, which is a thing no navigation does.
    get = Served(proxy=None, session="zoopla")
    get.get(ZOOPLA)
    get.get(ZOOPLA + "?pn=2")

    assert [one["referer"] for one in get.sent] == ["", ZOOPLA]
    get.close()


def test_a_page_that_was_refused_never_becomes_a_referer() -> None:
    # A browser has no referer for a page it never saw.
    get = Served(403, proxy=None, session="zoopla")
    with pytest.raises(Refused):
        get.get(ZOOPLA)
    assert get._seen == {}
    get.close()


def test_a_referer_is_never_carried_across_hosts() -> None:
    # The only cross-origin fetches here are images on a CDN, and what a
    # browser sends there is a different decision from this one.
    get = Served(proxy=None, session="zoopla")
    get.get(ZOOPLA)
    get.get(OPENRENT)

    assert get.sent[-1]["referer"] == ""
    get.close()


CHALLENGED = (
    "HTTP/2 403\r\n"
    "server: cloudflare\r\n"
    "cf-mitigated: challenge\r\n"
    "cf-ray: a4960ddd686b3692-LHR\r\n"
    "content-type: text/html\r\n"
)


def test_a_cloudflare_challenge_is_told_apart_from_a_cloudflare_block() -> None:
    # The whole reason the headers are kept. These two are both 403 and they
    # have opposite answers: a challenge is something a browser gets through,
    # so it is the reading under which a headless Chrome is worth its traffic;
    # a block is an address to stop using, and no browser helps with that.
    challenged = Served(403, CHALLENGED, proxy=None, session="zoopla")
    with pytest.raises(Refused) as raised:
        challenged.get(ZOOPLA)

    assert "cf-mitigated=challenge" in str(raised.value)
    assert "cf-ray=a4960ddd686b3692-LHR" in str(raised.value)
    # And in the counters, where a week of them can be counted rather than
    # read one log line at a time.
    assert any("cf-mitigated=challenge" in one for one in challenged.refusals)
    challenged.close()

    blocked = Served(403, "HTTP/2 403\r\nserver: cloudflare\r\n", proxy=None)
    with pytest.raises(Refused) as plain:
        blocked.get(ZOOPLA)

    assert "cf-mitigated" not in str(plain.value)
    assert "server=cloudflare" in str(plain.value)
    blocked.close()


def test_a_refusal_names_the_exit_address_it_came_from() -> None:
    # Without this the counters say a 403 happened and nothing about where,
    # which is the one fact that decides whether to rotate or to stop.
    get = Served(403, CHALLENGED, proxy=STICKY, session="zoopla", attempts=3)
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    paid = [one for one in get.refusals if "exit=" in one]
    assert paid, get.refusals
    assert all("direct" not in one for one in paid)
    get.close()


def test_a_served_page_carries_no_headers_to_parse() -> None:
    # 60KB of headers a run, parsed to be thrown away, would be a cost paid on
    # the path that is working.
    get = Served(200, CHALLENGED, proxy=None)
    assert get.get(ZOOPLA).headers == {}
    get.close()


def test_the_refusal_log_is_capped_rather_than_unbounded() -> None:
    # It ends up in a jsonb column, and twenty pages at five attempts each
    # would put a hundred lines in one counter.
    get = Served(403, CHALLENGED, proxy=None, session="zoopla")
    for page in range(KEPT_REFUSALS + 5):
        with pytest.raises(Refused):
            get.get(f"{ZOOPLA}?pn={page}")

    assert len(get.refusals) == KEPT_REFUSALS
    get.close()


# ── the backoff ────────────────────────────────────────────────────────────


def test_attempts_are_spaced_rather_than_fired_as_one_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cloudflare scores arrival, so three refused requests 200ms apart read as
    # one burst and the same three over seven seconds read as a retry. The
    # whole ladder is bounded: 0.5 doubling to a 4.0 cap.
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    get = Served(403, proxy=PROXY, session="zoopla", attempts=5, jitter=0)
    get.backoff = 0.5
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    # Two direct attempts then three paid ones, and the wait counts from the
    # start of each route rather than across both: moving to the proxy is a
    # different address on a different route, not a retry against the peer
    # that just refused us, so there is nothing there to back off from.
    assert slept == [0.5, 0.5, 1.0]
    assert all(one <= BACKOFF_CAP for one in slept)
    get.close()


def test_a_retry_after_the_portal_asks_for_is_honoured_over_our_own_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    asked = "HTTP/2 429\r\nserver: cloudflare\r\nretry-after: 3\r\n"
    get = Served(429, asked, proxy=None, session="zoopla", attempts=2, jitter=0)
    get.backoff = 0.5
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert slept == [3.0], "theirs is a number, ours is a guess"
    get.close()


def test_a_retry_after_longer_than_a_run_should_wait_ends_the_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A systemd oneshot asleep for ten minutes is a timer that has already
    # fired twice behind it. Coming back next run is the right answer.
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    asked = f"HTTP/2 429\r\nretry-after: {RETRY_AFTER_CAP + 600}\r\n"
    get = Served(429, asked, proxy=None, session="zoopla", attempts=5, jitter=0)
    get.backoff = 0.5
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert slept == []
    assert get.requests == 1
    get.close()


# ── the second provider ────────────────────────────────────────────────────
#
# One pool of residential addresses can go bad as a pool: the measured
# evidence this module is built on is that a refused exit stays refused while
# a different one serves the same request minutes later, but that only helps
# while *some* exit is being served. When `ATTEMPTS` is gone the thing that has
# been established is about the vendor, and the next thing to vary is whose
# addresses they are rather than which of theirs.
#
# It is off until a url is configured, and these pin that as hard as they pin
# the escalation itself: an unset fallback must cost nothing anywhere.

SMART = "http://user:pass@gate.smartproxy.net:10000"
SMART_ONE = "http://user:pass@gate.smartproxy.net:7000"


class Tiered(Fetcher):
    """A Fetcher that records which route each attempt went out on.

    Answers are keyed by route name, so a test can say "the primary refuses
    everything and the fallback serves" — which is the whole situation this
    exists for and the one thing `Fake` cannot express about two proxies.
    """

    def __init__(self, answers: dict[str, int], **kw: object) -> None:
        kw.setdefault("backoff", 0)
        kw.setdefault("jitter", 0)
        super().__init__(**kw)  # type: ignore[arg-type]
        self.answers = answers
        #: (route, the proxy url it actually used) per attempt.
        self.seen: list[tuple[str, str]] = []

    def _once(  # type: ignore[override]
        self, url: str, target: str, accept: str, via: str
    ) -> Reply:
        self._spent(1, via)
        self.seen.append((via, self._proxy_now(via)))
        return Reply(
            status=self.answers.get(via, 200), body="ok", wire=1, impersonated=target
        )

    def routes_used(self) -> list[str]:
        return [one for one, _ in self.seen]

    def ports_on(self, via: str) -> list[str]:
        return [url.rpartition(":")[2] for one, url in self.seen if one == via]


def test_an_unset_fallback_changes_nothing_at_all() -> None:
    # The default, and the property that makes this safe to deploy before the
    # url exists: no pool is built, no second memory file is read, and the
    # route list is exactly the length it was.
    get = Tiered({DIRECT: 403, PRIMARY: 403}, proxy=STICKY, session="zoopla")
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert get.fallback is None
    assert [one.name for one in get._pools] == [PRIMARY]
    assert FALLBACK not in get.routes_used()
    assert get.fallback_requests == 0
    assert get.fallback_port is None
    assert "fallback" not in str(raised.value)
    get.close()


def test_filling_in_the_url_is_the_whole_of_switching_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing else to set, nothing to migrate, no code change. This is the
    # requirement the route was built to: the variable goes in and the next run
    # has a second provider.
    monkeypatch.setenv("SCRAPE_PROXY", STICKY)
    monkeypatch.delenv("SCRAPE_PROXY_FALLBACK", raising=False)
    assert Fetcher.from_env(session="zoopla").fallback is None

    monkeypatch.setenv("SCRAPE_PROXY_FALLBACK", SMART)
    built = Fetcher.from_env(session="zoopla")
    assert built.fallback == SMART
    assert [one.name for one in built._pools] == [PRIMARY, FALLBACK]
    # And it got the sticky range off its own url, not the primary's.
    assert built.fallback_ports == tuple(STICKY_PORTS)
    assert built.fallback_attempts == FALLBACK_ATTEMPTS
    built.close()


def test_the_fallback_is_reached_only_once_the_primary_is_exhausted() -> None:
    # The order matters: the fallback is a second vendor's bill, so it is not
    # tried until the first has had its whole budget.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    assert get.get(ZOOPLA).status == 200

    assert get.routes_used() == [
        DIRECT, DIRECT, PRIMARY, PRIMARY, PRIMARY, FALLBACK
    ]
    # Two direct probes and three primary exits — the budget of five — then
    # the fallback, which is where the page actually came from.
    assert len(get.ports_on(PRIMARY)) == 3
    assert get.fallback_requests == 1
    get.close()


def test_the_fallback_gets_three_attempts_of_its_own() -> None:
    # Not a share of `ATTEMPTS`, which is empty by the time it is reached —
    # a budget the fallback had to share would be gone exactly when the
    # fallback became worth having. See FALLBACK_ATTEMPTS.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 403},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    with pytest.raises(Refused) as raised:
        get.get(ZOOPLA)

    assert len(get.ports_on(FALLBACK)) == FALLBACK_ATTEMPTS == 3
    assert len(get.seen) == 2 + 3 + 3, "the stated worst case for one url"
    # And the sentence says which provider ran out, because "the proxy
    # refused us" would now be ambiguous between two of them.
    assert "the fallback proxy" in str(raised.value)
    get.close()


def test_the_fallback_can_be_switched_off_without_losing_its_url() -> None:
    # What somebody bisecting a bad afternoon wants: stop paying the second
    # vendor without having to find the url again afterwards.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, fallback_attempts=0, session="zoopla",
    )
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert get.fallback == SMART, "still configured"
    assert [one.name for one in get._pools] == [PRIMARY]
    assert get.fallback_requests == 0
    get.close()


def test_each_pool_rotates_its_own_exits_and_never_the_others() -> None:
    # A port number names a different address on each vendor, so 10001 on one
    # says nothing whatever about 10001 on the other. Two pools, two cooldown
    # lists, two sticky exits.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 403},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    primary, second = get._pools
    assert primary.memory is not second.memory
    assert len(set(get.ports_on(PRIMARY))) == 3
    assert len(set(get.ports_on(FALLBACK))) == 3
    # Each pool's own burned ports went on its own list, and neither list
    # mentions the other's.
    assert primary.memory is not None and second.memory is not None
    burned_first = {str(one) for one in primary.memory.cooldown}
    burned_second = {str(one) for one in second.memory.cooldown}
    assert burned_first >= set(get.ports_on(PRIMARY))
    assert burned_second >= set(get.ports_on(FALLBACK))
    get.close()


def test_the_two_pools_keep_separate_memory_files(
    isolated_state: pathlib.Path,
) -> None:
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    get.get(ZOOPLA)
    get.close()

    assert (isolated_state / "exit-zoopla.json").is_file()
    assert (isolated_state / "exit-zoopla-fallback.json").is_file()


def test_the_fallbacks_exit_is_remembered_across_runs_like_the_primarys() -> None:
    # The sticky session is the point of all of this, and it is no less the
    # point for the second provider: an address that was served is the one the
    # next run should keep using.
    first = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    first.get(ZOOPLA)
    served = first.fallback_port
    first.close()

    second = Tiered({DIRECT: 200}, proxy=STICKY, fallback=SMART, session="zoopla")
    assert second.fallback_port == served
    second.close()


def test_the_fallbacks_traffic_is_metered_apart_from_the_primarys() -> None:
    # Two vendors send two invoices. `proxy_bytes` is reconciled against
    # DataImpulse's, so a figure mixing them could be checked against neither.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    get.get(ZOOPLA)

    assert get.proxied == 3 and get.proxy_wire == 3
    assert get.fallback_requests == 1 and get.fallback_wire == 1
    # The run's total still counts every byte, including the direct probes.
    assert get.requests == 6 and get.wire == 6
    get.close()


@pytest.mark.parametrize("status", [404, 500])
def test_an_answer_is_never_taken_to_a_second_vendor(status: int) -> None:
    # A 404 is an answer and a 500 is their problem. Asking a different
    # provider would spend a second vendor's traffic to hear it again.
    get = Tiered(
        {DIRECT: status, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert get.routes_used() == [DIRECT]
    assert get.fallback_requests == 0
    get.close()


def test_a_cdn_picture_reaches_neither_provider() -> None:
    # The rule that was true of one proxy has to stay true of two: a preview
    # image is an already-resized CDN variant served to anybody, and paying
    # two vendors to fetch the cheapest thing is worse than paying one.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 200, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
        direct_hosts=("media.rightmove.co.uk",),
    )
    with pytest.raises(Refused):
        get.get(IMAGE)

    # Every attempt on this address, varying only the fingerprint: with no
    # route to save the budget for, the direct ladder gets the whole of it.
    assert set(get.routes_used()) == {DIRECT}
    assert get.proxied == 0 and get.fallback_requests == 0
    get.close()


def test_a_blocked_host_skips_the_probe_and_still_reaches_both_providers() -> None:
    # The saved direct probe must not cost the escalation: a host known to
    # refuse this address goes straight to the primary and on to the fallback
    # from there.
    get = Tiered(
        {PRIMARY: 403, FALLBACK: 200},
        proxy=STICKY, fallback=SMART, session="zoopla",
    )
    get.blocked.add(host_of(ZOOPLA))
    assert get.get(ZOOPLA).status == 200

    assert get.routes_used() == [PRIMARY, PRIMARY, PRIMARY, PRIMARY, PRIMARY, FALLBACK]
    assert get.probes_skipped == 1
    get.close()


def test_a_single_exit_fallback_is_asked_three_times_on_its_one_address() -> None:
    # Their rotating port assigns an address per connection, so the fresh
    # tunnel is itself the new address and the port must not be moved — the
    # same rule as the primary's port 823.
    get = Tiered(
        {DIRECT: 403, PRIMARY: 403, FALLBACK: 403},
        proxy=STICKY, fallback=SMART_ONE, session="zoopla",
    )
    with pytest.raises(Refused):
        get.get(ZOOPLA)

    assert get.ports_on(FALLBACK) == ["7000", "7000", "7000"]
    get.close()


def test_only_the_configured_provider_is_used_when_the_primary_is_unset() -> None:
    # A box with only the second url filled in should work rather than
    # silently refuse to use it.
    get = Tiered({DIRECT: 403, FALLBACK: 200}, fallback=SMART, session="zoopla")

    assert [one.name for one in get._pools] == [FALLBACK]
    assert get.get(ZOOPLA).status == 200
    # Two direct probes, because there is somewhere to save the budget for,
    # and then the only provider configured.
    assert get.routes_used() == [DIRECT, DIRECT, FALLBACK]
    get.close()


def test_a_lookup_reaches_the_fallback_when_the_primary_refuses_too() -> None:
    # `head` spends one request per route rather than a ladder — `openrent`
    # makes hundreds of these against SLUG_BUDGET — so the fallback is one
    # more request, and only once the primary has refused as well.
    get = BreakingHead(7, breaks_through_proxy=True, proxy=STICKY, fallback=SMART)
    with pytest.raises(Refused) as raised:
        get.head(SHORT)

    assert get.vias == [DIRECT, PRIMARY, FALLBACK]
    assert "the fallback proxy" in str(raised.value)


def test_a_known_blocked_host_costs_one_lookup_per_provider_and_no_more() -> None:
    # The duplicate this closed: the switch to a paid route used to live
    # inside `_head_try`, so a host already in `blocked` got one lookup from
    # the direct call and then another from the escalation — the first
    # provider asked twice for one url. `openrent` makes hundreds of these a
    # run, so it was hundreds of duplicate paid requests.
    get = BreakingHead(7, breaks_through_proxy=True, proxy=STICKY, fallback=SMART)
    get.blocked.add(host_of(SHORT))
    with pytest.raises(Refused):
        get.head(SHORT)

    assert get.vias == [PRIMARY, FALLBACK], "one each, and no direct probe"
    assert get.probes_skipped == 1
