"""One way to fetch a page, for every portal that needs one.

Written against what the portals actually do, measured on 26 September 2026 and
recorded here because every number below is load-bearing:

  * Rightmove answers **403** to `urllib` with any User-Agent, and **200** to
    the same request from `curl_cffi` impersonating a browser. The block is on
    the TLS and HTTP/2 fingerprint, not on the address: this machine's ordinary
    connection is served once the handshake looks like Chrome's.
  * The fingerprint matters *which*: `chrome124` and `safari17_0` were served,
    `chrome131` was refused. So the impersonation target is configurable and
    rotated on refusal — that rotation is the real answer to being blocked.
  * They gzip whether or not it is asked for, about ten to one on a search page
    (119KB across the wire for 1.19MB of HTML). Brotli is not offered: asking
    for `br` alone returns the body uncompressed.
  * `robots.txt` permits `/property-to-rent/…` and `/properties/…`, and
    disallows `/api/*`. Nothing here touches the API.

── the byte count ────────────────────────────────────────────────────────

Residential proxies bill by traffic, so "how much did we download" has to be
the number that crossed the wire, not the number after decompression — they
differ by a factor of ten here. `len(response.content)` is the decoded size;
libcurl's `SIZE_DOWNLOAD_T` is the transfer. This module reports the transfer,
and adds `REQUEST_SIZE` because a proxy bills what goes up as well.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

from curl_cffi import Curl, CurlInfo, CurlOpt

# Served on 26 September 2026, in preference order. Rotated on a refusal rather
# than hard-coded to one, because a fingerprint that works today is a
# fingerprint somebody will add to a rule tomorrow.
IMPERSONATE = ("chrome124", "safari17_0", "chrome123", "edge101")

# Long enough for a slow portal, short enough that a run cannot stall behind
# one page.
TIMEOUT = 30.0

# Worth trying under a different browser fingerprint. These are the answers a
# TLS-fingerprint rule gives.
ROTATE = (401, 403, 429)

# Worth trying from a different address. Everything in ROTATE, plus 405:
# OpenRent answers 405 Method Not Allowed to a plain GET from a datacentre
# address while serving the identical request from a home connection, which is
# not a statement about the method.
#
# A 404 is deliberately absent. That is an answer, and asking again from
# somewhere else would spend traffic to receive it twice.
BLOCKED = (401, 403, 405, 429)


def host_of(url: str) -> str:
    """The hostname out of a url, lowercased and without its port."""

    return url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()


def _number(raw: object) -> int:
    """One of libcurl's counters as a whole number.

    `getinfo` is typed as returning any of bytes, a number, or a list, because
    the option decides which. Every option read here is numeric, and a counter
    that somehow came back as anything else is worth a zero rather than an
    exception in the middle of a fetch — the byte total is for billing, and a
    crash here would lose the page as well as the count.
    """

    return int(raw) if isinstance(raw, (int, float)) else 0


@dataclass
class Reply:
    """What came back, and what it cost."""

    status: int
    body: str
    #: Bytes across the wire — compressed, as billed. Not `len(body)`.
    wire: int
    #: Which impersonation target was served. Useful when one starts failing.
    impersonated: str


class Refused(Exception):
    """The portal would not serve this, under any fingerprint we have."""


@dataclass
class Fetcher:
    """A session with a byte meter.

    One instance per run, so `wire` and `requests` are that run's totals and
    can go straight into the stage counters.
    """

    #: Where to send requests. None is straight from this machine.
    proxy: str | None = None
    #: Never through the proxy, whatever `proxy` says. Images live on a CDN
    #: that serves anybody, and routing them through paid residential traffic
    #: is the most expensive way to fetch the cheapest thing.
    direct_hosts: tuple[str, ...] = ()
    timeout: float = TIMEOUT
    targets: tuple[str, ...] = IMPERSONATE

    #: Bytes across the wire, all requests. Compressed, as billed.
    wire: int = 0
    #: The billable subset: bytes that actually went through the proxy. The
    #: difference is what the CDN exemption saves, in the units DataImpulse
    #: charges in.
    proxy_wire: int = 0
    requests: int = 0
    proxied: int = 0
    #: The target that last worked, tried first next time.
    _best: str = field(default="", init=False)
    #: Hosts that refused this address, learned during this run. See `get`.
    #: A set rather than a setting: which portals block a datacentre changes
    #: without notice, and a config file that has to be edited when it does is
    #: a config file that will be wrong.
    blocked: set[str] = field(default_factory=set)

    #: One libcurl handle per impersonation target, kept open for the run.
    #:
    #: A handle holds its connection, so a sweep of twenty districts does one
    #: TLS handshake rather than twenty. Measured over four districts: 1.77s
    #: reused against 1.95s with a handle each, and no change in bytes — so
    #: this is about latency and about not knocking on the door sixty times a
    #: run, not about the bill.
    #:
    #: Keyed by target because `impersonate` configures the handle itself and
    #: a reset would undo it. In practice a run uses one: `_best` is sticky.
    _handles: dict[str, Curl] = field(default_factory=dict, init=False)

    @classmethod
    def from_env(cls) -> Fetcher:
        """Configured from the environment. No proxy set means no proxy used."""

        raw = (os.environ.get("SCRAPE_PROXY") or "").strip()
        targets = tuple(
            one.strip()
            for one in (os.environ.get("SCRAPE_IMPERSONATE") or "").split(",")
            if one.strip()
        )
        direct = tuple(
            one.strip().lower()
            for one in (os.environ.get("SCRAPE_DIRECT_HOSTS") or "").split(",")
            if one.strip()
        )
        return cls(
            proxy=raw or None,
            targets=targets or IMPERSONATE,
            direct_hosts=direct,
        )

    def never_proxy(self, url: str) -> bool:
        """Hosts that go direct whatever happens. See `direct_hosts`."""

        return any(
            host_of(url) == one or host_of(url).endswith("." + one)
            for one in self.direct_hosts
        )

    def _order(self) -> list[str]:
        return ([self._best] if self._best else []) + [
            one for one in self.targets if one != self._best
        ]

    def get(self, url: str, *, accept: str = "gzip, deflate") -> Reply:
        """Fetch one url. Direct if that works, through the proxy if it must.

        Raises `Refused` when nothing served it. Every attempt is metered,
        including the refused ones: a 403 through a residential proxy is
        traffic somebody charges for.

        ── why the proxy is an escalation and not a setting ─────────────────

        The three portals do not agree about this server. Measured from the
        server itself on 27 September 2026: Rightmove serves it, Zoopla answers
        403 under every fingerprint, OpenRent answers 405. From a home
        connection all three serve the identical requests, so what Zoopla and
        OpenRent object to is the address.

        Routing everything through the proxy to satisfy two of them would put
        Rightmove's traffic — the largest share — on a metered connection for
        no reason. So direct is tried first and the proxy is the answer to
        being refused, which means Rightmove stays free and the other two cost
        only what they have to.

        The refusal is remembered per host for the life of this Fetcher, so
        only the first district of a run pays the wasted probe. It is *not*
        remembered longer than that: a portal that stops blocking us goes back
        to being free by itself on the next run, with nothing to reconfigure.
        """

        host = host_of(url)
        if self.never_proxy(url) or not self.proxy:
            routes = [False]
        elif host in self.blocked:
            # Learned earlier this run. Skipping the probe saves one refused
            # request per district.
            routes = [True]
        else:
            routes = [False, True]

        last = 0
        for through_proxy in routes:
            for target in self._order():
                reply = self._once(url, target, accept, through_proxy)
                if 200 <= reply.status < 300:
                    self._best = target
                    return reply
                last = reply.status
                # Only a refusal is worth another fingerprint. A 404 is an
                # answer and a 500 is their problem — four handshakes against
                # either is four times the traffic for the same result.
                if reply.status not in ROTATE:
                    break
            if not through_proxy and last in BLOCKED:
                # Recorded whether or not there is anywhere to escalate to.
                # With no proxy configured this is the only evidence that the
                # portal objects to the address rather than to the request,
                # and that is exactly the case somebody needs told about.
                self.blocked.add(host)
                if len(routes) > 1:
                    continue
            break

        where = "the proxy" if routes[-1] else "this address"
        raise Refused(
            f"{url} answered {last} under every fingerprint tried, from {where}"
        )

    def head(self, url: str) -> tuple[int, str | None]:
        """Ask for the headers only. Returns the status and any `Location`.

        For the one question that a redirect answers more cheaply than a page
        does: OpenRent turns a bare listing id into its full slug url, and the
        slug carries the district, the bedroom count and the property type. The
        headers come to about 3KB where the page is 300KB, so this is a
        hundredfold saving on a question asked once per new listing.

        Not routed through the fingerprint rotation in `get`: a redirect is not
        something a portal refuses selectively, and if it does the caller can
        fall back to the page itself.
        """

        body = io.BytesIO()
        headers = io.BytesIO()
        # The same escalation as `get`, decided from what that already
        # learned: a host known to refuse this address is asked through the
        # proxy straight away.
        through_proxy = bool(self.proxy) and not self.never_proxy(url) and (
            host_of(url) in self.blocked
        )
        target = self._best or self.targets[0]
        curl = self._handle(target)

        curl.setopt(CurlOpt.URL, url.encode())
        curl.setopt(CurlOpt.WRITEDATA, body)
        curl.setopt(CurlOpt.HEADERDATA, headers)
        curl.setopt(CurlOpt.NOBODY, 1)
        # Deliberately not followed: the redirect *is* the answer, and
        # following it would fetch the very page this call exists to avoid.
        curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
        curl.setopt(CurlOpt.TIMEOUT_MS, int(self.timeout * 1000))
        curl.setopt(CurlOpt.PROXY, (self.proxy or "").encode() if through_proxy else b"")

        try:
            curl.perform()
            status = _number(curl.getinfo(CurlInfo.RESPONSE_CODE))
            wire = _number(curl.getinfo(CurlInfo.HEADER_SIZE)) + _number(
                curl.getinfo(CurlInfo.REQUEST_SIZE)
            )
        except Exception:
            self._handles.pop(target, None)
            curl.close()
            raise
        finally:
            # A handle is reused, and both of these would otherwise persist
            # and turn the next ordinary `get` into a bodiless request that
            # silently stops following redirects.
            curl.setopt(CurlOpt.NOBODY, 0)
            curl.setopt(CurlOpt.FOLLOWLOCATION, 1)
            curl.setopt(CurlOpt.HEADERDATA, io.BytesIO())

        self.requests += 1
        self.wire += wire
        if through_proxy:
            self.proxied += 1
            self.proxy_wire += wire

        where = None
        for line in headers.getvalue().decode("latin1").splitlines():
            if line.lower().startswith("location:"):
                where = line.split(":", 1)[1].strip()
        return status, where

    def _handle(self, target: str) -> Curl:
        handle = self._handles.get(target)
        if handle is None:
            handle = Curl()
            handle.impersonate(target)
            self._handles[target] = handle
        return handle

    def close(self) -> None:
        """Let go of every connection. Safe to call twice."""

        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _once(
        self, url: str, target: str, accept: str, through_proxy: bool
    ) -> Reply:
        body = io.BytesIO()
        curl = self._handle(target)

        # Every option is set on every request, none left to carry over. A
        # reused handle remembers what it was last told, and the one that
        # matters here is the proxy: a listing page goes through it and the
        # picture on the CDN must not, so "no proxy" has to be said rather
        # than left unsaid.
        curl.setopt(CurlOpt.URL, url.encode())
        curl.setopt(CurlOpt.WRITEDATA, body)
        curl.setopt(CurlOpt.ACCEPT_ENCODING, accept.encode())
        curl.setopt(CurlOpt.TIMEOUT_MS, int(self.timeout * 1000))
        curl.setopt(CurlOpt.FOLLOWLOCATION, 1)
        curl.setopt(CurlOpt.PROXY, (self.proxy or "").encode() if through_proxy else b"")

        try:
            curl.perform()
            status = _number(curl.getinfo(CurlInfo.RESPONSE_CODE))
            # The transfer, not the decompressed body. See the module note.
            wire = _number(curl.getinfo(CurlInfo.SIZE_DOWNLOAD_T)) + _number(
                curl.getinfo(CurlInfo.REQUEST_SIZE)
            )
        except Exception:
            # A handle that failed mid-transfer may be holding a connection in
            # an unknown state, and the next request on it would fail for a
            # reason that has nothing to do with the next request. Drop it; the
            # following call builds a fresh one.
            self._handles.pop(target, None)
            curl.close()
            raise

        self.requests += 1
        self.wire += wire
        if through_proxy:
            self.proxied += 1
            self.proxy_wire += wire
        return Reply(
            status=status,
            body=body.getvalue().decode("utf-8", errors="replace"),
            wire=wire,
            impersonated=target,
        )


__all__ = [
    "BLOCKED", "IMPERSONATE", "ROTATE", "TIMEOUT", "Fetcher", "Refused",
    "Reply", "host_of",
]
