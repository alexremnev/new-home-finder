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

from worker.sources.fetch import (
    BLOCKED,
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
