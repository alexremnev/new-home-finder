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

from dataclasses import dataclass

import pytest

from worker.sources.fetch import BLOCKED, ROTATE, Fetcher, Refused, Reply, host_of

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
