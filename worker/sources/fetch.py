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

── the exit address, and why one refusal is not an answer ──────────────────

Through the proxy, the thing worth varying is not the fingerprint — it is the
address. Zoopla serves a home connection every page under every fingerprint
and refuses this server under all of them, so a 403 from a residential exit is
a statement about that exit and about nothing else.

Measured from `job_events` on 9 October 2026, over `zoopla_london` runs on a
five-minute timer between 11:33 and 13:48:

    11:33 ✗  11:38 ✗  11:43 ✓  11:48 ✗  11:53 ✓  11:58 ✗
    12:03 ✗  12:08 ✗  12:13 ✓ … 12:43 ✓   (seven consecutive, served)
    12:48 ✗ … 13:18 ✗                     (seven consecutive, refused)
    13:23 ✓  13:28 ✗  13:33 ✓  13:38 ✗  13:43 ✓  13:48 ✗

A run refused at 13:28 was followed five minutes later by one served, so a
working request existed at the time and the refused run had no way to reach it.

Then the proxy itself was measured, the same day, against `api.ipify.org`:

    port 10000  →  95.147.142.82      one Fetcher, three requests:
    port 10001  →  78.150.17.32         95.147.142.82
    port 10002  →  86.38.26.95          95.147.142.82
    port 10000  →  95.147.142.82        95.147.142.82

Which says two things flatly. A port is a stable address — the fourth call was
a separate process and got the first one back. And one Fetcher holds one
address for its whole life, because a handle holds its tunnel and the tunnel
holds the session.

── what that means together, and what is still a guess ─────────────────────

`SCRAPE_PROXY` named port 10000, so until this change *every run of both
Zoopla readers went out from 95.147.142.82 and nothing else*. The alternation
above therefore cannot be an address being refused: there was only ever one.
Zoopla's answer on a fixed address varied through the day instead, in clumps —
seven runs served, then seven refused. A 403 rather than a 429 is what a bot
score gives, so the likeliest reading is a score with hysteresis, fed by twelve
runs an hour from one house, plus the district reader's bursts on :07/:27/:47
through the very same session.

What is NOT established: how long one sticky session lasts. The measurement
above spans seconds, so a session that rolls every half hour would look
identical to one that never rolls, and that is the difference between "the
address varied through the day" and "it did not". Re-running the same check an
hour later settles it, and nothing below depends on the answer.

── what follows for the code, under any of those readings ──────────────────

Spread the traffic and be able to ask again:

  * The exit is no longer the configured port. A run starts at an offset into
    the sticky range, so consecutive runs of one reader and concurrent runs of
    two do not share an address — which takes the per-address request rate
    from twelve an hour to roughly nothing, and that alone is most of the fix
    if the reading above is right.
  * A refusal through the proxy is retried on a different exit, up to `EXITS`
    of them, and the fingerprint is left alone. Inverting the old ladder —
    four fingerprints against one address — costs nothing extra: it spent
    three paid 403s per failing run learning what the first one had said.

How a different exit is asked for depends on the port, and both are handled by
dropping every handle so the next request opens a new tunnel:

  * a sticky port (10000-10500) holds one session per port, so the port is
    moved as well — reconnecting on the same one returns the same session,
    which is exactly what the right-hand column above shows.
  * the rotating port (823) assigns per connection, so the fresh tunnel is by
    itself the new address. Connection reuse is what used to defeat it.

── the byte count ────────────────────────────────────────────────────────

Residential proxies bill by traffic, so "how much did we download" has to be
the number that crossed the wire, not the number after decompression — they
differ by a factor of ten here. `len(response.content)` is the decoded size;
libcurl's `SIZE_DOWNLOAD_T` is the transfer. So this counts that, plus
`HEADER_SIZE` for the response headers and `REQUEST_SIZE` for what went up: a
proxy bills every byte through the tunnel and does not care which part of the
exchange they belong to.

── what it still cannot see, and by how much ───────────────────────────────

The CONNECT exchange and the TLS handshake inside the tunnel. libcurl's
counters are HTTP-level and those bytes are below it, so nothing here can
report them — and they are real: a certificate chain alone is several KB, and
it is paid once per tunnel, so a run that changes fingerprint or exit address
pays it again.

Measured against a DataImpulse invoice on 8 October 2026: three proxied
requests came to 67KB by this counter and 90.49KB on theirs. Response headers
accounted for about 9KB of the 23KB gap — that part is now counted — and the
rest was three handshakes. So expect this figure to read a few per cent under
the invoice, not a third under it, and expect the gap to widen if the proxy is
set to a rotating port rather than a sticky one.
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

# ── the rule that makes a reused handle safe ────────────────────────────────
#
# curl_cffi's `setopt(WRITEDATA, f)` and `setopt(HEADERDATA, f)` wrap `f` in a
# cffi handle and hand libcurl the raw pointer. At the END of every `perform()`
# it runs `clean_handles_and_buffers()`, which drops the Python references that
# were keeping those handles alive — while libcurl still holds the pointers and
# still has the callbacks installed.
#
# So on a reused handle, any `perform()` that does not re-set a callback target
# it installed earlier will call that callback with a dangling pointer, and
# cffi kills the interpreter outright:
#
#     Fatal Python error: b_from_handle: ffi.from_handle() detected that the
#     address passed points to garbage
#
# That is not catchable. It happened here: `head()` installed HEADERDATA, and
# the second ordinary `get()` after it — the first one re-registered by
# accident — fired the header callback into freed memory and took the whole run
# with it. Windows reported exit -1073740791.
#
# Hence: EVERY request sets EVERY callback target, on every path. Add a third
# one and it has to be set in both `_once` and `head` too.
CALLBACK_TARGETS = ("WRITEDATA", "HEADERDATA")

# Worth trying from a different address. Everything in ROTATE, plus 405:
# OpenRent answers 405 Method Not Allowed to a plain GET from a datacentre
# address while serving the identical request from a home connection, which is
# not a statement about the method.
#
# A 404 is deliberately absent. That is an answer, and asking again from
# somewhere else would spend traffic to receive it twice.
BLOCKED = (401, 403, 405, 429)

# DataImpulse's sticky range, from their dashboard's "Get proxy" panel. One
# port is one session is one exit address — measured on 9 October 2026, and
# measured to be stable: two calls to port 10000 from different processes got
# 95.147.142.82 both times while 10001 and 10002 got different addresses. So a
# different port in this range is the way to ask for another address without
# giving up stickiness altogether.
#
# Anything outside the range is left alone: 823 is their rotating port and
# assigns an address per connection, which a fresh tunnel already gets.
STICKY_PORTS = range(10000, 10501)

# Exit addresses to try for one url before calling it refused.
#
# Three is a judgement and not a calculation, because the thing it is hedging
# against is not measured: the refusals in the module note all came from one
# address, so there is no per-exit refusal rate to put a number on, and
# whatever it is will be lower once a reader is no longer making twelve
# requests an hour from one house.
#
# What is known is the shape of the cost. Each extra exit is one CONNECT, one
# handshake and one paid 403, on a request already lost — and the old ladder
# spent four of those on fingerprints, so three is still cheaper than what it
# replaces. Should the counter in the stage show rotations routinely reaching
# three and still failing, the answer is not a fourth: it is that the exit is
# not the variable, and `SCRAPE_IMPERSONATE` is the next thing to move.
EXITS = 3


def host_of(url: str) -> str:
    """The hostname out of a url, lowercased and without its port."""

    return url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()


def _port_of(proxy: str) -> int | None:
    """The port a proxy url states, or None when it states none."""

    tail = proxy.rpartition(":")[2]
    return int(tail) if tail.isdigit() else None


def _with_port(proxy: str, port: int) -> str:
    """The same proxy url on a different port. Only valid when it had one."""

    return f"{proxy.rpartition(':')[0]}:{port}"


def exit_ports(proxy: str | None) -> tuple[int, ...]:
    """The ports this proxy may rotate through.

    A sticky port gets the whole sticky range: every port in it is a separate
    session and so a separate address, so the one that was configured is only
    evidence of which kind of setup this is. Anything else — the rotating port,
    a proxy with no port at all, no proxy — gets at most the port it already
    has, because for those the address does not follow the port and moving it
    would only point at one nobody is listening on.
    """

    port = _port_of(proxy) if proxy else None
    if port is None:
        return ()
    return tuple(STICKY_PORTS) if port in STICKY_PORTS else (port,)


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
    #: Exit addresses to try for one url before calling it refused. One turns
    #: the rotation off without turning the proxy off.
    exits: int = EXITS
    #: Ports to rotate through, one exit address each. Derived from `proxy` in
    #: `__post_init__` when it is left empty, which is every caller but a test.
    ports: tuple[int, ...] = ()

    #: Bytes across the wire, all requests. Compressed, as billed.
    wire: int = 0
    #: The billable subset: bytes that actually went through the proxy. The
    #: difference is what the CDN exemption saves, in the units DataImpulse
    #: charges in.
    proxy_wire: int = 0
    requests: int = 0
    proxied: int = 0
    #: Exit addresses given up on this run. Goes into the stage counters: a
    #: reader that rotates every run is a pool going bad, which is a different
    #: piece of news from a run that failed.
    rotations: int = 0
    #: The target that last worked, tried first next time.
    _best: str = field(default="", init=False)
    #: Which of `ports` is in use. Advanced by `_rotate` and left there: an
    #: exit that was served is the one the rest of the run should keep using.
    _exit: int = field(default=0, init=False)
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

    def __post_init__(self) -> None:
        if not self.ports:
            self.ports = exit_ports(self.proxy)
        # Where in the range this run starts, and deliberately not the port
        # that was configured.
        #
        # Three readers scrape through this proxy and two of them are Zoopla.
        # Started from one configured port they would share one sticky session
        # and therefore one address — so a single burned exit takes all three
        # out at once, and the whole day's traffic arrives at the portal from
        # one house. Offsetting by pid makes them independent for free: it
        # differs between concurrent processes and between runs, and needs no
        # RNG and no state.
        if self.ports:
            self._exit = os.getpid() % len(self.ports)

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
        exits = (os.environ.get("SCRAPE_EXITS") or "").strip()
        return cls(
            proxy=raw or None,
            targets=targets or IMPERSONATE,
            direct_hosts=direct,
            exits=int(exits) if exits.isdigit() else EXITS,
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

    def _proxy_now(self) -> str:
        """The proxy url to use, on the exit address currently in play."""

        if not self.proxy or not self.ports:
            return self.proxy or ""
        return _with_port(self.proxy, self.ports[self._exit % len(self.ports)])

    def _rotate(self) -> None:
        """Give up on this exit address and move to another one.

        Every handle is dropped, which is what actually gets a new address: a
        handle holds its tunnel, the tunnel holds the session, and the session
        holds the exit. Changing the port alone would be enough on a sticky
        setup — libcurl keys its connection cache on the proxy — but dropping
        the handles is the one move that works for the rotating port too, and
        it is already this module's idiom for a connection it no longer trusts.

        The cost is one CONNECT and one TLS handshake, a few KB, paid on a
        request that was going to be refused otherwise. There is no cap here:
        `sweep.REFUSALS_ALLOWED` stops a run after three districts in a row
        have been refused, which bounds this at nine rotations however badly
        the pool is going.

        Handles are keyed by fingerprint and not by host, so this also drops
        any direct connection the run was holding to somewhere else. That is
        accepted rather than overlooked: the only job that reads two portals in
        one process is `portals`, which nothing schedules, and the connection
        it would have to make again is a free one.
        """

        self.rotations += 1
        self._exit += 1
        self.close()

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

        ── what each route varies, and why they differ ─────────────────────

        Direct, the fingerprint: rotating it is free, and a 403 from this
        address could be either a fingerprint rule or an address rule, so the
        cheap suspect is eliminated first.

        Through the proxy, the exit address: by then the fingerprint has
        already been cleared on the direct route, and the measured evidence is
        that a refused exit stays refused while a different one serves the
        identical request minutes later. See the module note for the figures.

        One consequence worth stating, because it is a real trade and not an
        oversight: a fingerprint rule that Zoopla adds *tomorrow* would now
        present as "every exit refused" rather than as anything naming a
        fingerprint. `SCRAPE_IMPERSONATE` is the way to tell the two apart, and
        it is already documented as the thing to try before the proxy.
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
        tried = 0
        paid = False
        for through_proxy in routes:
            paid = through_proxy
            # One fingerprint against several addresses on the paid route, and
            # several fingerprints against one address on the free one.
            attempts = (
                [self._best or self.targets[0]] * max(1, self.exits)
                if through_proxy
                else self._order()
            )
            for nth, target in enumerate(attempts):
                # Between attempts rather than after the last one, so a run
                # that is about to give up does not pay for a tunnel it will
                # never use. Only on the paid route: on the direct one it is
                # the fingerprint that `attempts` is varying, and the address
                # is the one thing there that cannot be changed.
                if nth and through_proxy:
                    self._rotate()
                reply = self._once(url, target, accept, through_proxy)
                tried = nth + 1
                if 200 <= reply.status < 300:
                    self._best = target
                    return reply
                last = reply.status
                # Only a refusal is worth another try. A 404 is an answer and a
                # 500 is their problem — four handshakes against either is four
                # times the traffic for the same result.
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

        # Named for what was actually varied, because this sentence is what
        # ends up in `job_events` and is all a later investigation has to go
        # on. It used to say "every fingerprint tried" on both routes, which
        # on the paid one was not true even then.
        #
        # Keyed on the route that was *reached*, not on the last one available.
        # A 500 on the direct probe stops there — it is their fault and not
        # worth paying to hear twice — and the old wording read the end of
        # `routes` and so blamed a proxy the request never went near.
        if paid:
            where = "the proxy"
            over = "exit address" if tried == 1 else "exit addresses"
        else:
            where = "this address"
            over = "fingerprint" if tried == 1 else "fingerprints"
        raise Refused(
            f"{url} answered {last} under {tried} {over} tried, from {where}"
        )

    def head(self, url: str) -> tuple[int, str | None]:
        """Ask for the headers only. Returns the status and any `Location`.

        For the one question that a redirect answers more cheaply than a page
        does: OpenRent turns a bare listing id into its full slug url, and the
        slug carries the district, the bedroom count and the property type. The
        headers come to about 3KB where the page is 300KB, so this is a
        hundredfold saving on a question asked once per new listing.

        Not routed through the fingerprint rotation: a redirect is not something
        a portal refuses by fingerprint. It IS something a portal refuses by
        address, though, and that is what this escalates on.

        ── why it has to escalate, and what happened when it did not ─────────

        OpenRent answers this server 405 on a listing url and serves the
        identical request from a home connection — measured, and the reason
        `BLOCKED` carries 405 at all. `get` knew that and escalated; this did
        not, because it only reached for the proxy when some earlier `get` had
        already been refused by the same host. On `openrent` no earlier
        `get` ever is: the search page is served, and only the per-listing
        lookups are refused. So every lookup came back 405, forever, with no
        proxy attempt — which is most of why that reader stored 134 listings
        in a fortnight and announced none of them.
        """

        status, where = self._head_once(url, through_proxy=False)
        if status in BLOCKED:
            # Remembered for the rest of the run, exactly as `get` does, so the
            # next few hundred lookups go straight through the proxy instead of
            # each paying for its own refusal first.
            self.blocked.add(host_of(url))
            if self.proxy and not self.never_proxy(url):
                return self._head_once(url, through_proxy=True)
        return status, where

    def _head_once(self, url: str, *, through_proxy: bool) -> tuple[int, str | None]:
        body = io.BytesIO()
        headers = io.BytesIO()
        # A host already known to refuse this address skips the direct probe
        # altogether — `head` passes True and this does as it is told.
        if not through_proxy and self.proxy and not self.never_proxy(url):
            through_proxy = host_of(url) in self.blocked
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
        curl.setopt(
            CurlOpt.PROXY, self._proxy_now().encode() if through_proxy else b""
        )

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
            # A reused handle remembers these, and a bodiless request that
            # stops following redirects is not what the next `get` wants.
            # `_once` sets them too, belt and braces, because a `head()` that
            # raised never reaches this block.
            #
            # HEADERDATA is deliberately NOT reset to a fresh buffer here. That
            # is what the first version did, and it was the bug: the throwaway
            # was registered, then freed after the next perform, and the perform
            # after that called the header callback on the dead pointer and
            # killed the interpreter. Nothing needs resetting — `_once` sets a
            # live buffer before every perform. See CALLBACK_TARGETS.
            curl.setopt(CurlOpt.NOBODY, 0)
            curl.setopt(CurlOpt.FOLLOWLOCATION, 1)

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
        # Set even though nothing reads it here. A previous `head()` on this
        # handle left the header callback installed, and curl_cffi drops the
        # reference that kept its target alive at the end of every perform —
        # so not setting it means firing that callback into freed memory. See
        # CALLBACK_TARGETS.
        headers = io.BytesIO()
        curl = self._handle(target)

        # Every option is set on every request, none left to carry over. A
        # reused handle remembers what it was last told, and the one that
        # matters here is the proxy: a listing page goes through it and the
        # picture on the CDN must not, so "no proxy" has to be said rather
        # than left unsaid.
        curl.setopt(CurlOpt.URL, url.encode())
        curl.setopt(CurlOpt.WRITEDATA, body)
        curl.setopt(CurlOpt.HEADERDATA, headers)
        curl.setopt(CurlOpt.ACCEPT_ENCODING, accept.encode())
        curl.setopt(CurlOpt.TIMEOUT_MS, int(self.timeout * 1000))
        curl.setopt(CurlOpt.FOLLOWLOCATION, 1)
        # Undone here rather than trusted to have been undone by whoever set
        # it: a `head()` that raised part-way leaves the handle bodiless, and
        # every page after it would come back empty with no error at all.
        curl.setopt(CurlOpt.NOBODY, 0)
        curl.setopt(
            CurlOpt.PROXY, self._proxy_now().encode() if through_proxy else b""
        )

        try:
            curl.perform()
            status = _number(curl.getinfo(CurlInfo.RESPONSE_CODE))
            # The transfer, not the decompressed body. See the module note.
            # Body, response headers, request headers — all three, because a
            # proxy bills every byte through the tunnel and does not care
            # which part of the exchange they belong to. The response headers
            # were missing and they are not small: measured 6.6KB against a
            # 72.7KB body on a Zoopla search page, nine per cent, every
            # request. See the module note on what is still not counted.
            wire = (
                _number(curl.getinfo(CurlInfo.SIZE_DOWNLOAD_T))
                + _number(curl.getinfo(CurlInfo.HEADER_SIZE))
                + _number(curl.getinfo(CurlInfo.REQUEST_SIZE))
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
    "BLOCKED", "CALLBACK_TARGETS", "EXITS", "IMPERSONATE", "ROTATE",
    "STICKY_PORTS", "TIMEOUT",
    "Fetcher", "Refused", "Reply", "exit_ports", "host_of",
]
