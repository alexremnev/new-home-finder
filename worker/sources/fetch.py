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

`SCRAPE_PROXY` named port 10000, so until this change *every proxied request
this server ever made went out from 95.147.142.82 and nothing else*. The
alternation above therefore cannot be an address being refused: there was only
ever one address. Zoopla's answer on it varied through the day instead, in
clumps — seven runs served, then seven refused.

What it is not, is us outstaying our welcome. `zoopla_london` is the only
reader that proxies from here — the district sweep is run by hand from a
laptop, which Zoopla serves directly and which therefore never touches this
session — and it fetches one 60KB page every five minutes. Twelve pages an
hour from a residential address is not a rate anybody throttles.

So the score on that address is most likely not only ours. A sticky session
pins *our* peer, not the peer's other traffic, and a residential device
carrying somebody else's Zoopla scraping would show up here exactly like this:
clumps, on one address, with no relation to anything we did. That is inference
and not measurement — but every reading of it says the same thing about the
code, because all of them are about having only ever held one ticket.

What is NOT established: how long one sticky session lasts. The measurement
above spans seconds, so a session that rolls every half hour would look
identical to one that never rolls, and that is the difference between "the
address varied through the day" and "it did not". Re-running the same check an
hour later settles it, and nothing below depends on the answer.

── what follows for the code, under any of those readings ──────────────────

Spread the traffic and be able to ask again:

  * The exit is no longer the configured port. It is drawn from the sticky
    range per *reader*, remembered across runs, and kept for as long as it is
    served. See `worker.sources.session`, and read the next section before
    changing it: this was an offset by pid for three days in October, which
    spread the traffic per *run* instead and was worse than what it replaced.
  * A refusal through the proxy is retried on a different exit, within the
    `ATTEMPTS` budget, and the fingerprint is left alone. Inverting the old
    ladder — four fingerprints against one address — costs nothing extra: it
    spent three paid 403s per failing run learning what the first one said.
  * So is a request that got no answer at all — a tunnel the proxy would not
    open, a certificate it could not verify, a peer that never replied. Those
    arrive as an exception rather than a status and so went past the rotation
    entirely, taking the whole run with them. See BROKEN.

How a different exit is asked for depends on the port, and both are handled by
dropping every handle so the next request opens a new tunnel:

  * a sticky port (10000-10500) holds one session per port, so the port is
    moved as well — reconnecting on the same one returns the same session,
    which is exactly what the right-hand column above shows.
  * the rotating port (823) assigns per connection, so the fresh tunnel is by
    itself the new address. Connection reuse is what used to defeat it.

── what none of the above was about, and what it cost ─────────────────────

Everything above treats a refusal as a question about *where the request came
from*. Measured on 12 October 2026, after a morning in which `zoopla_london`
recorded no served run between 10:00 and 12:00, two things turned out to be
true that nothing above accounts for.

**Zoopla is behind Cloudflare Bot Management, and this module threw its token
away.** The London search page answers with `server: cloudflare`, a `cf-ray`,
and four Set-Cookie headers — `__cf_bm`, `_cfuvid`, `ajs_anonymous_id`,
`_needs_refresh`. `__cf_bm` is the bot-management token and it lives 1800
seconds. There was no cookie handling anywhere in this module, so every single
request was a first-ever visit.

How much that matters is measurable rather than arguable. Through one jar file
and two separate libcurl handles:

    cold, no jar          4 x Set-Cookie
    warm, jar on disk     1 x Set-Cookie   (a refreshed __cf_bm, nothing else)

Three successive processes sharing a jar kept one `ajs_anonymous_id` and one
`_cfuvid` throughout. So the jar is the difference between arriving as a
visitor the site has seen and arriving as one it has not, and a 30-minute
token against a 5-minute timer means one warm session spans six runs — but
only if something outlives the process, which on a `Type=oneshot` unit nothing
did. Hence `session.Session` and `StateDirectory=` in the unit.

**And the impersonation targets had gone stale.** The list was `chrome124,
safari17_0, chrome123, edge101`: Chrome 124 is April 2024 and Edge 101 is April
2022, so in October 2026 every request claimed to be a browser two to four
years old. Cloudflare reads that directly. The installed curl_cffi (0.16.3)
had chrome150, chrome146, firefox147 and safari2601 available the whole time.

The two compound, which is the part worth keeping hold of: a brand-new
residential address, with no cookies, claiming to be a two-year-old Chrome, is
three bad signals arriving together, and the pid offset was *adding* the first
one every five minutes.

What is NOT established: how much each of the three contributed. From a home
connection all of chrome124, chrome150 and firefox147 are served 200, so none
of this can be reproduced off the server — the way to apportion it is the
counters, and `refusal_detail` is the one that can finally tell a Cloudflare
challenge from a Cloudflare block. See EVIDENCE.

── when the pool itself is the problem ─────────────────────────────────────

Everything above varies something about *our* request: the fingerprint, the
cookies, which of one vendor's exits carries it. All of it assumes some
address in that vendor's pool is being served, and that assumption is the last
one left to fail — a residential pool is somebody else's inventory, shared
with whoever else is renting it, and it can be scored badly as a pool.

So there is a second provider, reached only once the first has spent its whole
budget: `PRIMARY` and `FALLBACK`, three attempts on the second, its own sticky
exit, its own cooldown list, its own cookie jars and its own line on the bill.
See `Pool` for why none of those can be shared, and FALLBACK_ATTEMPTS for why
the budget is additional rather than divided.

It is off until `SCRAPE_PROXY_FALLBACK` names a url, and off means absent: no
pool is built, no second state file is read, and the route lists below are the
length they were. That is deliberate — the route was written before there was
an account to point it at, so the untested path had to be the one that does
nothing.

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
import random
import time
from dataclasses import dataclass, field

from curl_cffi import Curl, CurlECode, CurlError, CurlInfo, CurlOpt

# Aliased because `Fetcher.session` is the *name* of one of these and the two
# reading alike in the same method would be the kind of confusion that gets
# one of them silently right.
from worker.sources import session as session_memory

# Which browser to look like, in preference order. Rotated on a refusal rather
# than hard-coded to one, because a fingerprint that works today is a
# fingerprint somebody will add to a rule tomorrow.
#
# ── why these four and not the four that were here ──────────────────────────
#
# The old list was `chrome124, safari17_0, chrome123, edge101`, chosen on
# 26 September 2026 because those were the ones measured to be served. What it
# missed is that a fingerprint does not only have to be *accepted* — it has to
# be *current*, and those were not. Chrome 124 shipped in April 2024 and
# Edge 101 in April 2022, so by October 2026 the list was claiming to be a
# browser two to four years old on every request. Cloudflare reads the age of
# a user agent directly; it is the cheapest signal it has.
#
# Checked against the installed curl_cffi on 12 October 2026 — version 0.16.3,
# whose targets include chrome150, chrome146, chrome145, firefox147 and
# safari2601 — so nothing here needed a dependency bump, only a list that had
# stopped being maintained.
#
# ── one UA, one TLS fingerprint, one decision ───────────────────────────────
#
# It is worth being explicit that this tuple *is* the user-agent rotation, and
# that there is deliberately no separate User-Agent setting anywhere in this
# module. `Curl.impersonate(target)` moves the whole coherent set together —
# measured on chrome150, what goes out is:
#
#     user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...
#                 Chrome/150.0.0.0 Safari/537.36
#     sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"
#     sec-ch-ua-platform: "macOS"        + the full sec-fetch-* set
#
# plus the JA3/JA4 TLS fingerprint and the HTTP/2 SETTINGS frame to match.
# Setting a User-Agent header by hand would move one of those and leave the
# other eleven where they were, and a UA that disagrees with the TLS handshake
# is a stronger signal than a UA that is merely old. So the rotation is of the
# target, never of the header.
#
# Two engines rather than four Chromiums, for the same reason the list is
# rotated at all: if Zoopla adds a rule keyed on Chrome's fingerprint,
# `firefox147` is the attempt that tells us so.
IMPERSONATE = ("chrome150", "firefox147", "chrome146", "safari2601")

# Long enough for a slow portal, short enough that a run cannot stall behind
# one page.
#
# Fifteen rather than thirty because the ladder above it got longer. A page
# that is going to be served arrives in well under a second — measured over
# four districts on 27 September 2026, 1.77s for all four on a reused handle —
# so fifteen is already thirty times the measured figure, and nothing between
# fifteen and thirty seconds has ever been observed to arrive. What the old
# value cost was the failing case: with `EXITS` addresses to try, each one
# allowed to hang for thirty seconds, one url could hold a run for minutes
# against systemd's `TimeoutStartSec=600` and the next fire of a five-minute
# timer. Halving it halves that worst case and loses nothing real.
TIMEOUT = 15.0

# Seconds of random delay before each request, drawn fresh every time.
#
# Drawn per request rather than once per run, because one delay chosen at the
# start moves a burst and leaves its shape alone — and the shape is the whole
# point. The pages of one sweep otherwise arrive back-to-back as fast as a
# reused handle can ask for them, twenty-six requests in one run as measured on
# 10 October 2026.
#
# ── why four tenths and not a second and a half ─────────────────────────────
#
# Because the size of the delay is not what does the work; the variance is.
# Six sequential fetches of Zoopla's London search page from a home connection
# on 10 October 2026, jitter off:
#
#     435ms  186  182  159  193  358     median 190ms, stdev 115ms
#
# So requests already arrive between 159 and 435 milliseconds apart with
# nothing added, which says two things at once. A jitter of a few tens of
# milliseconds would vanish into that and buy nothing. And a jitter of seconds
# is not buying four times as much either — past the point where our variance
# matches the connection's, more only costs time.
#
# `uniform(0, 0.4)` has a standard deviation of 115ms, which is the measured
# figure exactly, so it doubles the variance of the whole series. That is the
# anchor: as much irregularity again as the network already provides. Raising
# it to 0.75 would make ours dominate at about twice the connection's, which is
# available and was not chosen — nothing measured says the portals key on
# request timing at all. Zoopla's refusals are per exit address and clumped in
# time, and a home connection making the identical requests at any spacing is
# served every one.
#
# The cost, at a mean of `JITTER / 2` a request, against the per-run ceilings:
#
#   a two-page zoopla_london read     2 requests    ~0.4s
#   the largest sweep measured       26 requests     ~5s
#   sweep's own ceilings             ~90 requests    ~18s   DISTRICT_BUDGET
#                                                           plus POSTCODE_BUDGET
#   openrent's slug backlog         400 requests     ~80s   SLUG_BUDGET
#
# All four are comfortable — the last one is what made the old value awkward,
# at five minutes of a backlog run spent asleep.
#
# It is not a rate limit and not a substitute for one: it is only the removal
# of a pattern, and a small one. The run cadence is the part a portal could
# actually read off a stopwatch, and that is handled where it belongs — the
# three readers' timers are offset by a third of their interval each, and carry
# RandomizedDelaySec on top. `sweep.PAUSE_SECONDS` is the third delay in play,
# a flat second between districts, deliberately left flat: it is politeness
# toward the portal rather than camouflage.
JITTER = 0.4

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

# ── when there is no answer at all ─────────────────────────────────────────
#
# Everything above is a *status*: the portal replied, and the reply is the
# evidence. These are the other outcome — libcurl could not finish the
# exchange, so there is no status to read and `perform()` raises instead.
#
# Three of them, measured on `zoopla_london` on 11 October 2026:
#
#     curl: (60) SSL certificate ... unable to get local issuer certificate
#     curl: (7)  CONNECT tunnel failed, response 502
#     curl: (28) Connection timed out after 15002 milliseconds
#
# Every one is a statement about one exit address and about nothing else: a
# tunnel the proxy would not open, a certificate presented by whatever
# answered inside it, a peer that never replied at all. That is the same kind
# of news as a 403 through the proxy, and it has the same answer — ask a
# different address.
#
# It was not being asked. `CurlError` is neither `Refused` nor `OSError`, so
# it went past the rotation in `get`, past every `except (Refused, OSError)`
# at the call sites, and out through `run.stage` as "scrape failed" — which
# ended the whole run on its first district with two exits unused and the
# other nineteen districts unread. One bad address cost a run.
#
# Listed rather than caught wholesale, because the two kinds must not be
# confused: a malformed url or a bad option is OUR bug, and a bug that fails
# loudly on the first run is worth far more than one folded away into "the
# portal refused us". Anything not named here still raises.
BROKEN = frozenset(
    {
        CurlECode.COULDNT_RESOLVE_PROXY,     # 5
        CurlECode.COULDNT_RESOLVE_HOST,      # 6
        CurlECode.COULDNT_CONNECT,           # 7  — a refused CONNECT, too
        CurlECode.HTTP2,                     # 16
        CurlECode.PARTIAL_FILE,              # 18
        CurlECode.OPERATION_TIMEDOUT,        # 28
        CurlECode.SSL_CONNECT_ERROR,         # 35
        CurlECode.GOT_NOTHING,               # 52
        CurlECode.SEND_ERROR,                # 55
        CurlECode.RECV_ERROR,                # 56
        CurlECode.PEER_FAILED_VERIFICATION,  # 60 — the exit's chain, not ours
        CurlECode.HTTP2_STREAM,              # 92
        CurlECode.HTTP3,                     # 95
        CurlECode.PROXY,                     # 97
    }
)

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

# Attempts for one url before calling it refused, both routes together.
#
# One budget rather than two, which is the change: the old code allowed
# `self._order()` fingerprints on the direct route and then `EXITS` exit
# addresses on the paid one, so the real ceiling was four plus three — seven
# requests for one page, and nobody reading either constant would have said
# so. Now the number here is the number of requests, and the split between the
# routes comes out of it.
#
# Five is a judgement, and what it is hedging against is still not measured:
# the refusals in the module note all came from one address, so there is no
# per-exit refusal rate to put a number on. What is known is the shape of the
# cost — each extra attempt is one CONNECT, one TLS handshake and one paid 403
# on a request already lost. Should `exit_rotations` routinely reach the
# ceiling and the run still fail, the answer is not a sixth attempt: it is
# that the exit address is not the variable, and `SCRAPE_IMPERSONATE` is the
# next thing to move.
ATTEMPTS = 5

# How many of `ATTEMPTS` may be spent on the free route when a proxy exists.
#
# Two, and it is evidence-gathering rather than hope. A 403 from this machine's
# own address could be a fingerprint rule or an address rule, and one attempt
# cannot tell them apart; two under different engines can, because a
# fingerprint rule that catches `chrome150` will not catch `firefox147`. Past
# two it stops being evidence and starts being three more requests from an
# address that has already said no twice.
#
# With no proxy configured this does not apply and the whole budget goes on the
# direct route: there is nowhere to save it for.
DIRECT_PROBES = 2

# Seconds to wait before the first retry, doubling after that up to the cap.
#
# The old code rotated instantly. For a connection that never opened that is
# right — a dead tunnel is dead now — but for a *status* it is the one thing
# every rate-limit guide agrees on, and the reason is mechanical rather than
# mystical: Cloudflare scores request arrival, so three refused requests
# 200ms apart read as one burst, and the same three spread over seven seconds
# read as somebody retrying.
#
# 0.5s doubling to a 4s cap is at most 7.5s across a fully failing ladder of
# five. Measured against the budgets this has to fit inside: `zoopla_london`
# reads one or two pages a run against a five-minute timer and systemd's
# `TimeoutStartSec=600`, and a district sweep is stopped after three refused
# districts by `sweep.REFUSALS_ALLOWED`, which bounds the whole run's waiting
# at about twenty seconds. Both are comfortable.
#
# Not applied in `head`: that has no ladder to space out — one direct attempt
# and one through the proxy — and it is the call `openrent` makes hundreds of
# times in a run, where half a second each would be minutes of a backlog run
# spent asleep for no evidence at all.
BACKOFF = 0.5
BACKOFF_CAP = 4.0

# The longest `Retry-After` worth honouring, in seconds.
#
# A portal that asks for a short wait is telling us something useful and it is
# cheaper to obey than to be refused again. One that asks for ten minutes is
# telling us to come back in a later run, and sleeping through it would hold a
# systemd oneshot open past its timer — so past this it is treated as a
# refusal and the url is left for next time.
RETRY_AFTER_CAP = 10.0

# Response headers kept from a refusal, lowercased.
#
# This is the whole of P1-5: without it a 403 is indistinguishable from a 403,
# and those are two different problems with two different answers.
#
#   * `cf-mitigated: challenge` means Cloudflare served an interactive
#     challenge rather than a block. A browser would get through it, so that
#     is the reading under which a headless Chrome is worth its 40x traffic.
#   * a 403 with no `cf-mitigated` at all is the exit address being scored
#     badly, and the answer to that is a different address, never a browser.
#   * `cf-ray` identifies the edge node and the request, which is the only
#     thing Cloudflare support will accept as a reference.
#   * `server` says who refused us, so the day Zoopla moves off Cloudflare
#     this stops silently meaning nothing.
#   * `retry-after` is read rather than guessed at. See RETRY_AFTER_CAP.
EVIDENCE = ("cf-mitigated", "cf-ray", "server", "retry-after")

# Refusals described in the stage counters, most recent last. Capped because
# this ends up in a jsonb column: five attempts a page against twenty pages
# would otherwise put a hundred lines in one counter, and the first ten are
# enough to tell a challenge from a block.
KEPT_REFUSALS = 10

# ── the three places a request can go out from ─────────────────────────────
#
# Names rather than a boolean, which is what these replaced. `through_proxy`
# could say "not the proxy" and "the proxy" and had nowhere to put a second
# one — and a second one is the whole point of this: when every exit in one
# provider's pool has refused us, the next thing to vary is not the address,
# it is whose addresses they are.
#
# They are strings because they are also filenames: a cookie jar is keyed per
# route, and `__cf_bm` minted through one vendor's peer must never be presented
# through another's. See `session.Session.jar`.
DIRECT = "direct"
PRIMARY = "primary"
FALLBACK = "fallback"

# Attempts the fallback provider gets, on top of `ATTEMPTS`.
#
# ── why on top rather than out of it ───────────────────────────────────────
#
# `ATTEMPTS` is a budget against one pool: five requests spent learning that
# this machine's address and three of one vendor's exits will not serve us. By
# the time it is exhausted, what has been established is that *that vendor* is
# not working right now — and a budget the fallback had to share would be
# empty exactly when the fallback became worth having.
#
# So the worst case for one url is `ATTEMPTS + FALLBACK_ATTEMPTS`, eight
# requests, and that is stated plainly because it is more than the five the
# budget above advertises. It is reachable only when the direct address, three
# primary exits and three fallback exits all refuse the same page, which is a
# run that was going to fail anyway — and the eighth request is the one that
# might stop it failing. Set `SCRAPE_FALLBACK_ATTEMPTS=0` to turn the whole
# route off without unsetting its url.
#
# Three because that is what a second pool is for. One attempt makes the
# fallback a coin toss on a single exit, and the measured evidence this module
# was built on is that exits vary: a refused one stays refused while a
# different one serves the identical request minutes later.
FALLBACK_ATTEMPTS = 3


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


def _seconds(raw: str | None, fallback: float) -> float:
    """A number of seconds out of the environment, or the fallback.

    Anything unreadable is the fallback rather than an exception: a typo in
    `SCRAPE_JITTER` should not stop every reader on the box, and zero is a
    meaningful value here so it cannot double as "unset".
    """

    try:
        value = float((raw or "").strip())
    except ValueError:
        return fallback
    return value if value >= 0 else fallback


def _number(raw: object) -> int:
    """One of libcurl's counters as a whole number.

    `getinfo` is typed as returning any of bytes, a number, or a list, because
    the option decides which. Every option read here is numeric, and a counter
    that somehow came back as anything else is worth a zero rather than an
    exception in the middle of a fetch — the byte total is for billing, and a
    crash here would lose the page as well as the count.
    """

    return int(raw) if isinstance(raw, (int, float)) else 0


def _wire(curl: Curl) -> int:
    """Bytes across the wire for the transfer just attempted.

    The transfer, not the decompressed body — see the module note. Body,
    response headers and request headers, all three, because a proxy bills
    every byte through the tunnel and does not care which part of the exchange
    they belong to. The response headers were once missing and they are not
    small: measured 6.6KB against a 72.7KB body on a Zoopla search page, nine
    per cent, every request.

    Zero is a possible honest answer on a transfer that failed before any of
    those existed, and reading the meter must never replace the error that is
    on its way up: `getinfo` on a handle whose `perform` raised is not
    documented to work, so a refusal to answer here is taken as nothing spent.
    """

    try:
        return (
            _number(curl.getinfo(CurlInfo.SIZE_DOWNLOAD_T))
            + _number(curl.getinfo(CurlInfo.HEADER_SIZE))
            + _number(curl.getinfo(CurlInfo.REQUEST_SIZE))
        )
    except Exception:
        return 0


def _evidence(raw: bytes) -> dict[str, str]:
    """The few response headers worth keeping, out of the raw block.

    Parsed here rather than with an HTTP library because there is no HTTP
    library in this process — the whole point of `curl_cffi` is that libcurl
    does the protocol — and because what is wanted is four names out of fifty.

    `latin1` rather than utf-8: header bytes are latin1 by RFC 7230 and a
    `cf-ray` that failed to decode would lose the diagnosis to an exception
    raised while explaining a refusal. The LAST occurrence of a name wins,
    which is what matters after a redirect: the headers of the reply that was
    actually returned come last in the block.
    """

    found: dict[str, str] = {}
    for line in raw.decode("latin1").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() in EVIDENCE:
            found[name.strip().lower()] = value.strip()
    return found


def _named(via: str) -> str:
    """A route, as it reads in the middle of a sentence in `job_events`.

    One place so that `get` and `head` cannot drift apart about what to call
    the second provider — the sentence they build is the whole of what a later
    investigation has to go on.
    """

    if via == DIRECT:
        return "this address"
    return "the proxy" if via == PRIMARY else "the fallback proxy"


def _broke(exc: CurlError) -> str:
    """Why there was no answer, in a sentence. Re-raises anything that is ours.

    One place decides which libcurl errors are worth another address, so `get`
    and `head` cannot drift apart about it. See BROKEN for the list and for
    why the rest are left to fail loudly.

    curl_cffi's message is three sentences and the last two are a link to the
    error table plus an instruction to go and read it. The first carries the
    code and the detail, and that is the part worth putting in `job_events`:

        Failed to perform, curl: (60) SSL certificate OpenSSL verify result:
        unable to get local issuer certificate (20). See https://curl.se/...
        first for more details.

    becomes `curl: (60) SSL certificate OpenSSL verify result: unable to get
    local issuer certificate (20)` — which reads as a clause in the middle of
    the sentence the caller is building, hence no full stop.
    """

    if exc.code not in BROKEN:
        raise exc
    said = str(exc).split("See https://", 1)[0].strip()
    said = said.removeprefix("Failed to perform,").strip().rstrip(".")
    return said or f"curl: ({int(exc.code)})"


@dataclass
class Reply:
    """What came back, and what it cost."""

    status: int
    body: str
    #: Bytes across the wire — compressed, as billed. Not `len(body)`.
    wire: int
    #: Which impersonation target was served. Useful when one starts failing.
    impersonated: str
    #: The few response headers worth keeping. See EVIDENCE. Empty on a reply
    #: that carried none of them, which is every served page.
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def retry_after(self) -> float:
        """What the portal asked us to wait, in seconds. Zero when it did not.

        Only the numeric form is read. `Retry-After` also allows an HTTP date,
        which no portal here has ever sent, and parsing one to discover we
        should wait past `RETRY_AFTER_CAP` anyway would be work for nothing.
        """

        try:
            return max(0.0, float(self.headers.get("retry-after", "")))
        except ValueError:
            return 0.0

    @property
    def said(self) -> str:
        """Just the headers worth quoting, as `name=value` pairs.

        In EVIDENCE's order rather than the wire's, so two refusals from
        different edges read alike and the eye can run down a column of them.
        """

        return " ".join(
            f"{name}={self.headers[name]}" for name in EVIDENCE
            if name in self.headers
        )

    def evidence(self, *, exit_port: int | None) -> str:
        """One line about a refusal, for the stage counters. See EVIDENCE."""

        where = f"exit={exit_port}" if exit_port else "direct"
        return f"{self.status} {where} {self.impersonated} {self.said}".strip()


@dataclass
class Pool:
    """One residential proxy, and this reader's memory of its exit addresses.

    There are at most two of these — a primary and a fallback — and they exist
    because the pools are not interchangeable in any respect that matters:

      * a port number means a different address on each vendor, so one
        cooldown list covering both would retire addresses that were never
        refused and keep offering ones that were;
      * a cookie is minted against the peer that asked for it, so a jar has to
        belong to a vendor as well as to an exit and a browser;
      * and the bill is separate. `proxy_bytes` is checked against an invoice,
        and a figure mixing two vendors cannot be checked against either.

    So each one carries its own `Session` file, its own current exit and its
    own counters, and the only thing they share is the reader they belong to.
    """

    #: `PRIMARY` or `FALLBACK`. Names the route in the counters, in the cookie
    #: jar filenames and in the sentence that reaches `job_events`.
    name: str
    #: The proxy url, with whatever port was configured. Never empty: a pool
    #: with no url is not built at all.
    proxy: str
    #: Attempts this pool gets for one url. Zero means the pool is configured
    #: but switched off, which is a thing somebody debugging wants to be able
    #: to do without editing `SCRAPE_PROXY_FALLBACK` out and losing it.
    attempts: int
    #: One exit address each. Derived from `proxy` by `exit_ports`.
    ports: tuple[int, ...] = ()
    #: Which of `ports` is in play, remembered across runs by `memory`.
    port: int | None = None
    #: Requests sent through this pool, and what they weighed. Per pool
    #: because two vendors send two invoices.
    requests: int = 0
    wire: int = 0
    #: Exit addresses this pool gave up on during this run.
    rotations: int = 0
    #: This pool's sticky exit and cooldown list, across runs.
    memory: session_memory.Session | None = None

    def proxy_now(self) -> str:
        """The proxy url on the exit address currently in play."""

        if self.port is None:
            return self.proxy
        return _with_port(self.proxy, self.port)

    def address(self) -> str:
        """What the request will go out from, named for a filename."""

        return f"{self.name}-{self.port}" if self.port is not None else self.name


class Refused(Exception):
    """We could not get this page, and no further attempt here would help.

    Two causes, deliberately one exception. Either the portal answered and
    the answer was a refusal, or nothing answered at all — a tunnel that
    would not open, a handshake that failed, a peer that never replied.

    One type because every call site wants the same thing from both: count it,
    say so, leave the district incomplete, and come back next run. Splitting
    them would mean a second `except` clause at six places, all of which
    already exist and none of which would do anything different.
    """


@dataclass
class Fetcher:
    """A session with a byte meter.

    One instance per run, so `wire` and `requests` are that run's totals and
    can go straight into the stage counters.
    """

    #: Where to send requests when this machine's own address is refused.
    #: None is straight from this machine and nowhere else.
    proxy: str | None = None
    #: A second residential provider, tried only once `proxy`'s whole budget
    #: has been spent. None — which is the default and what an unset
    #: `SCRAPE_PROXY_FALLBACK` gives — means there is no such route and
    #: nothing anywhere behaves differently.
    #:
    #: Two vendors rather than more exits from one, because by the time
    #: `ATTEMPTS` is gone the thing that has been established is about the
    #: vendor: a pool whose addresses are all scored badly is a pool, and
    #: asking it a fourth time is asking the same question again. See
    #: FALLBACK_ATTEMPTS.
    fallback: str | None = None
    #: Never through the proxy, whatever `proxy` says. Images live on a CDN
    #: that serves anybody, and routing them through paid residential traffic
    #: is the most expensive way to fetch the cheapest thing.
    direct_hosts: tuple[str, ...] = ()
    timeout: float = TIMEOUT
    #: Seconds of random delay before each request. Zero turns it off, which is
    #: what a test wants and nothing in production does.
    jitter: float = JITTER
    targets: tuple[str, ...] = IMPERSONATE
    #: Attempts for one url before calling it refused, both routes together.
    #: One turns every ladder off without turning the proxy off.
    attempts: int = ATTEMPTS
    #: How many of them may go on the free route when a proxy exists.
    direct_probes: int = DIRECT_PROBES
    #: Attempts the fallback provider gets, on top of `attempts`. Zero turns
    #: the route off while leaving its url configured.
    fallback_attempts: int = FALLBACK_ATTEMPTS
    #: Ports to rotate through, one exit address each. Derived from `proxy` in
    #: `__post_init__` when it is left empty, which is every caller but a test.
    ports: tuple[int, ...] = ()
    #: The same for `fallback`, derived from it the same way.
    fallback_ports: tuple[int, ...] = ()
    #: Which reader this is, for the cross-run memory. One name is one sticky
    #: exit address and one set of cookie jars, so two readers under one name
    #: would share an address — which is the thing the pid offset this
    #: replaces was written to prevent. `sweep.collect` passes the portal key.
    session: str = "default"
    #: Seconds to wait before the first retry, doubling after. Zero turns the
    #: backoff off, which is what a test wants.
    backoff: float = BACKOFF
    #: False keeps every cookie in memory for the life of this Fetcher, which
    #: is what a test wants and what a run with nowhere writable gets anyway.
    cookies: bool = True

    #: Bytes across the wire, all requests. Compressed, as billed.
    wire: int = 0
    #: The billable subset: bytes that actually went through the *primary*
    #: proxy. The difference from `wire` is what the CDN exemption saves, in
    #: the units DataImpulse charges in.
    #:
    #: Deliberately not the sum across both providers. This figure exists to
    #: be checked against an invoice, and one invoice comes from one vendor —
    #: a number mixing them could be reconciled against neither. The fallback's
    #: traffic is reported beside it, never folded into it.
    proxy_wire: int = 0
    requests: int = 0
    proxied: int = 0
    #: Exit addresses given up on this run, across both pools. Goes into the
    #: stage counters: a reader that rotates every run is a pool going bad,
    #: which is a different piece of news from a run that failed.
    rotations: int = 0
    #: Refused attempts, described. One line each, see `Reply.evidence`.
    #: `sweep` writes these to the stage counters, and they are the only thing
    #: that can tell a Cloudflare challenge from a Cloudflare block.
    refusals: list[str] = field(default_factory=list)
    #: Direct probes not made because an earlier run had already learned the
    #: host refuses this address. One saved request each. See `_memory`.
    probes_skipped: int = 0
    #: The target that last worked, tried first next time.
    _best: str = field(default="", init=False)
    #: The paid routes in the order they are escalated to, built from `proxy`
    #: and `fallback` in `__post_init__`. Empty with neither configured, which
    #: is when nothing but the direct route exists.
    #:
    #: Each holds its own exit address, chosen by its own cross-run memory
    #: rather than from this process's pid: an exit that was served is one the
    #: *next* run should keep using, not one this run should forget.
    _pools: tuple[Pool, ...] = field(default=(), init=False)
    #: What survives the process, for the reader as a whole. Owns `blocked` —
    #: which hosts refuse *this machine's* address, a fact about the box and
    #: not about any vendor — and the direct route's cookie jar. Each pool
    #: carries a second file of its own for its exits. See
    #: worker.sources.session.
    _memory: session_memory.Session = field(init=False, repr=False)
    #: Host → the last url on it that was served, sent as `Referer` on the
    #: next request to the same host. See `_once`.
    _seen: dict[str, str] = field(default_factory=dict, init=False, repr=False)
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
    #: Keyed by target *and route*, because both configure the handle and
    #: neither can be reset per request: `impersonate` sets the fingerprint
    #: once, and the cookie jar is a file this handle alone may write. See
    #: `_slot`. In practice a run uses one or two: `_best` is sticky and a
    #: refused host goes straight to the proxy.
    _handles: dict[str, Curl] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.ports:
            self.ports = exit_ports(self.proxy)
        if not self.fallback_ports:
            self.fallback_ports = exit_ports(self.fallback)
        # ── what replaced the pid offset, and why ──────────────────────────
        #
        # This used to be `os.getpid() % len(self.ports)`. The reasoning was
        # sound as far as it went: one configured sticky port meant every
        # proxied request this server ever made went out from one address, so
        # the load had to be spread. What it got wrong was the axis. The pid
        # of a `Type=oneshot` unit is arbitrary, so spreading by pid spread it
        # *per run* — a fresh residential address every five minutes, with no
        # cookies and no history, which is the single worst thing to present to
        # a bot score. Measured on 12 October 2026: Zoopla answers a cold
        # request with four Set-Cookie headers and a warm one with one.
        #
        # So the spread moved from per-run to per-reader. The memory is named,
        # one file per reader, which keeps the three readers on three
        # addresses — the property the offset existed for — while letting each
        # one hold its own address across runs for as long as it is served.
        self._memory = session_memory.Session.load(self.session)
        # One pool per configured provider, in escalation order, each with its
        # own memory file. Built here rather than looked up per request so that
        # "is there anywhere else to ask" is one question with one answer, and
        # so that an unset `fallback` costs nothing anywhere: no file is read,
        # no pool exists, and every route list below is the length it was.
        #
        # A pool with no url is not built at all, which is what makes adding
        # `SCRAPE_PROXY_FALLBACK` to the environment sufficient to switch the
        # whole route on with no other change.
        pools = []
        for name, url, ports, budget in (
            (PRIMARY, self.proxy, self.ports, self.attempts),
            (FALLBACK, self.fallback, self.fallback_ports, self.fallback_attempts),
        ):
            if not url or budget <= 0:
                continue
            pool = Pool(name=name, proxy=url, attempts=budget, ports=ports)
            # The reader's own file holds `blocked` and the direct jar; this
            # one holds nothing but this vendor's exits. Separate because a
            # port number names a different address on each vendor, so one
            # cooldown list would be wrong about both.
            pool.memory = (
                self._memory
                if name == PRIMARY
                else session_memory.Session.load(f"{self.session}-{name}")
            )
            pool.port = pool.memory.pick(ports)
            pools.append(pool)
        self._pools = tuple(pools)
        # Hosts an earlier run found refuse this machine's own address. Seeded
        # rather than re-learned: the probe that learns it is a request whose
        # answer has been the same since September. A host pinned to the direct
        # route is excluded — there is nowhere to escalate it to, so skipping
        # its probe would mean never reading it at all.
        self.blocked |= {
            one for one in self._memory.blocked
            if not self.never_proxy(f"https://{one}/")
        }
        self._memory.prune_jars()

    @classmethod
    def from_env(cls, *, session: str = "default") -> Fetcher:
        """Configured from the environment. No proxy set means no proxy used.

        `session` names the cross-run memory and comes from the caller rather
        than the environment: it is a property of which reader is running, not
        of how the box is configured, and getting it from `SCRAPE_SESSION`
        would mean every reader on one box sharing one exit address.
        """

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
        # `SCRAPE_EXITS` is read as a fallback and not documented any more.
        # It named the paid route's ladder; `SCRAPE_ATTEMPTS` names the whole
        # budget, which is a smaller number for the same behaviour. Honoured
        # so that a box with the old variable set does not silently change
        # behaviour on deploy — but it is the total now, which is the point.
        budget = (
            os.environ.get("SCRAPE_ATTEMPTS") or os.environ.get("SCRAPE_EXITS") or ""
        ).strip()
        # The second provider. Empty is the normal case and means the route
        # does not exist — no file, no pool, no behaviour change anywhere.
        # Filling it in is the whole of switching it on.
        second = (os.environ.get("SCRAPE_PROXY_FALLBACK") or "").strip()
        # Read separately from the url so the route can be switched off
        # without losing the url, which is what somebody bisecting a bad
        # afternoon wants. `0` is meaningful here and so cannot double as
        # "unset" — hence `isdigit` rather than a falsy check.
        spare = (os.environ.get("SCRAPE_FALLBACK_ATTEMPTS") or "").strip()
        return cls(
            proxy=raw or None,
            fallback=second or None,
            targets=targets or IMPERSONATE,
            direct_hosts=direct,
            attempts=int(budget) if budget.isdigit() and int(budget) else ATTEMPTS,
            fallback_attempts=(
                int(spare) if spare.isdigit() else FALLBACK_ATTEMPTS
            ),
            jitter=_seconds(os.environ.get("SCRAPE_JITTER"), JITTER),
            session=session,
        )

    def _pool(self, via: str) -> Pool | None:
        """The pool a route name refers to, or None for the direct route."""

        for one in self._pools:
            if one.name == via:
                return one
        return None

    @property
    def exit_port(self) -> int | None:
        """Which primary exit address the run is on, for the stage counters.

        Read-only and public where the pool's own field is neither: `sweep`
        has to be able to report it — two runs in a row on one port is the
        mechanism working and a new port every run is it failing — but nothing
        outside this module may choose it. That is `session.Session`'s job.

        The primary specifically, so a week of these can be read as one
        series. The fallback's is reported beside it and only when the
        fallback was actually reached, which is the news.
        """

        pool = self._pool(PRIMARY)
        return pool.port if pool else None

    @property
    def fallback_port(self) -> int | None:
        """The same for the second provider, or None when there is none."""

        pool = self._pool(FALLBACK)
        return pool.port if pool else None

    @property
    def fallback_requests(self) -> int:
        """Requests that went through the second provider.

        Non-zero is the thing worth alerting on: it means the primary pool
        spent its whole budget and still did not get the page. A reader that
        reaches here occasionally has one bad exit; a reader that reaches here
        every run has a vendor that has stopped working.
        """

        pool = self._pool(FALLBACK)
        return pool.requests if pool else 0

    @property
    def fallback_wire(self) -> int:
        """Bytes through the second provider, for that vendor's own invoice."""

        pool = self._pool(FALLBACK)
        return pool.wire if pool else 0

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

    def _ladder(self, room: int, via: str, *, escalates: bool) -> list[str]:
        """What to try on this route, within what is left of its budget.

        The routes vary different things, and they always have.

        Through a proxy, the exit address: by then the fingerprint has been
        cleared on the direct route, and the measured evidence is that a
        refused exit stays refused while a different one serves the identical
        request minutes later. Both pools work this way — the fallback is not
        a different mechanism, only a different pool of addresses, which is
        why it needs no logic of its own.

        Direct, the fingerprint: rotating it is free, and a 403 from this
        address could be either rule, so the cheap suspect goes first. Capped
        at `direct_probes` when there is a paid route to save the budget for —
        here it is evidence, there it is the page.
        """

        if via != DIRECT:
            return [self._best or self.targets[0]] * room
        cap = min(room, max(1, self.direct_probes)) if escalates else room
        return self._order()[:cap]

    def _backoff(self, nth: int, asked: float) -> None:
        """Wait before asking again. See BACKOFF and RETRY_AFTER_CAP.

        `asked` is what the portal's own `Retry-After` named, and it wins when
        it is longer: a wait we chose is a guess and a wait they named is not.
        """

        grown = (
            min(self.backoff * 2 ** (nth - 1), BACKOFF_CAP) if self.backoff > 0 else 0.0
        )
        delay = max(grown, min(asked, RETRY_AFTER_CAP))
        if delay > 0:
            time.sleep(delay)

    def _note(self, said: str) -> None:
        """Keep a line about one refused attempt. See KEPT_REFUSALS.

        The *first* few rather than the last few. A run that fails is usually
        explained by how it started failing — the first refusal names the exit
        address and the fingerprint that were in play before any rotation
        muddied them — and the tenth is the ninth again.
        """

        if len(self.refusals) < KEPT_REFUSALS:
            self.refusals.append(said)

    def _served(self, host: str, url: str, *, via: str) -> None:
        """Record a page that actually arrived.

        Two things follow from a 2xx and nothing follows from anything else.

        The referer chain: the next request to this host carries this url, the
        way a browser carries the page a link was on. Only a page that *was*
        served, because a browser has no referer for a page it never saw.

        And, on the direct route, that this host has stopped refusing us —
        forgotten in the cross-run memory too, so the next run goes back to
        reading it for free rather than paying the proxy out of habit.
        """

        self._seen[host] = url
        if via == DIRECT and host in self.blocked:
            self.blocked.discard(host)
            self._memory.forget(host)
            self._memory.save()

    def _proxy_now(self, via: str) -> str:
        """The proxy url to use, on the exit address currently in play."""

        pool = self._pool(via)
        return pool.proxy_now() if pool else ""

    def _slot(self, target: str, via: str) -> str:
        """Which handle serves this target on the route in play.

        Target and address both, because a handle carries a cookie jar and a
        jar belongs to exactly one of each — see `session.Session.jar` for why
        a cookie minted on one exit under one user agent must not be presented
        on another. One slot is one jar file, so no two handles can write the
        same file and lose each other's cookies when they close.

        The address names the vendor as well as the port, because the two
        pools number their exits independently: `primary-10001` and
        `fallback-10001` are two different houses, and a jar shared between
        them would be a cookie presented from the wrong one.
        """

        return f"{target}@{self._address(via)}"

    def _address(self, via: str) -> str:
        """What the request will go out from, named for a filename."""

        pool = self._pool(via)
        return pool.address() if pool else DIRECT

    def _rotate(self, via: str) -> None:
        """Give up on this pool's exit address and move to another of its own.

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

        pool = self._pool(via)
        if pool is None or pool.memory is None:
            # The direct route, which has one address and cannot change it.
            # Reached only through a caller that should not have asked, so
            # doing nothing beats inventing a rotation that did not happen.
            return
        self.rotations += 1
        pool.rotations += 1
        # Through the memory rather than an index along the range, and the
        # difference is not cosmetic: the old walk advanced an index, so on a
        # 501-port range it could — and over a day would — come back round to
        # an address it had already been refused by. `burn` puts the abandoned
        # port on cooldown for an hour and draws one that is not on it.
        #
        # Against this pool's own cooldown list, never the other's: a port
        # number names a different address on each vendor, so burning 10001 on
        # one says nothing at all about 10001 on the other.
        pool.port = pool.memory.burn(pool.ports)
        # Written out now rather than at the end of the run, because the thing
        # that most wants to know this exit is bad is the *next* run, and a
        # run that is rotating is a run that may be about to be killed by its
        # own timeout.
        pool.memory.save()
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
        paid_routes = [one.name for one in self._pools]
        if self.never_proxy(url) or not paid_routes:
            # A CDN image, or a box with no provider configured at all. Either
            # way this address is the only one there is.
            routes = [DIRECT]
        elif host in self.blocked:
            # Learned this run — or by an earlier one, and seeded in
            # `__post_init__`. Skipping the probe saves one refused request
            # per district, and on Zoopla, whose answer to this address has
            # not changed since September, it also saves the first one of the
            # run: that is the request the old code paid for every five
            # minutes to be told what it already knew. See `BLOCK_FOR`.
            routes = paid_routes
            self.probes_skipped += 1
        else:
            routes = [DIRECT, *paid_routes]

        # What the last attempt came back with: a status, or — when nothing
        # came back at all — libcurl's reason for that. Two variables rather
        # than one, because `last in BLOCKED` below is a question about a
        # status, and a string there would quietly answer no.
        last = 0
        broke = ""
        tried = 0
        paid = ""
        #: The headers the last refusal carried, for the sentence at the
        #: bottom. See EVIDENCE.
        said = ""
        #: What the portal asked us to wait, if it asked. See RETRY_AFTER_CAP.
        wait = 0.0
        #: Attempts made on the direct route and the primary pool, which share
        #: one budget. The fallback keeps its own — see FALLBACK_ATTEMPTS — so
        #: it is deliberately not counted here.
        spent = 0
        for via in routes:
            paid = "" if via == DIRECT else via
            # The fallback gets its own budget, because by here the five above
            # are gone and what they established is about the *other* vendor.
            room = (
                self.fallback_attempts
                if via == FALLBACK
                else self.attempts - spent
            )
            if room <= 0:
                # The free route used the lot. Only reachable with no proxy
                # configured — `direct_probes` leaves room when there is one —
                # but said here rather than relied upon.
                break
            attempts = self._ladder(room, via, escalates=len(routes) > 1)
            for nth, target in enumerate(attempts):
                if nth:
                    # Two different things, and only the first is route-bound.
                    #
                    # The new exit address, between attempts rather than after
                    # the last one, so a run about to give up does not pay for
                    # a tunnel it will never use — and only on a paid route,
                    # because on the direct one it is the fingerprint that
                    # `attempts` varies and the address is the one thing there
                    # that cannot be changed.
                    #
                    # The wait, on every route. It is about how the requests
                    # arrive rather than about where from, and a burst of
                    # fingerprints off one address is as readable as a burst of
                    # addresses. See BACKOFF.
                    if via != DIRECT:
                        self._rotate(via)
                    self._backoff(nth, wait)
                # Counted before the attempt, not after it: an attempt that
                # raises is still an attempt, and the sentence at the bottom
                # of this method is the only account of it anybody gets.
                tried = nth + 1
                if via != FALLBACK:
                    spent += 1
                try:
                    reply = self._once(url, target, accept, via)
                except CurlError as exc:
                    # No status to judge — the exchange never completed. See
                    # BROKEN for which of these earn another address, and for
                    # which still raise through this as our own bug.
                    broke = _broke(exc)
                    last = 0
                    wait = 0.0
                    self._note(f"- {self._address(via)} {target} {broke}")
                    # On a paid route the next attempt is a new exit address,
                    # which is exactly what a tunnel that would not open calls
                    # for. On the free route the loop varies the fingerprint
                    # instead, and a connection that never opened is no
                    # evidence about a fingerprint — so stop here and let the
                    # escalation below do the work.
                    if via == DIRECT:
                        break
                    continue
                broke = ""
                if 200 <= reply.status < 300:
                    self._best = target
                    self._served(host, url, via=via)
                    return reply
                last = reply.status
                said = reply.said
                pool = self._pool(via)
                self._note(reply.evidence(exit_port=pool.port if pool else None))
                wait = reply.retry_after
                if wait > RETRY_AFTER_CAP:
                    # They named a wait longer than this run should be held
                    # open for. Obeying it is the next run's job: a systemd
                    # oneshot asleep for ten minutes is a timer that has
                    # already fired twice behind it.
                    break
                # Only a refusal is worth another try. A 404 is an answer and a
                # 500 is their problem — four handshakes against either is four
                # times the traffic for the same result.
                if reply.status not in ROTATE:
                    break
            if via == DIRECT:
                if broke and len(routes) > 1:
                    # A portal that null-routes this address looks exactly
                    # like a moment of bad network from here, and a proxy is
                    # the only other place to ask — so ask.
                    #
                    # Deliberately NOT recorded in `blocked`, which is the one
                    # difference from the refusal below. That memory sends the
                    # rest of the run straight to paid traffic, and a timeout
                    # is not evidence enough to spend money on nineteen more
                    # districts; a refusal is, because a portal that says 403
                    # to this address says it every time.
                    continue
                if last in BLOCKED:
                    # Recorded whether or not there is anywhere to escalate
                    # to. With no proxy configured this is the only evidence
                    # that the portal objects to the address rather than to
                    # the request, and that is exactly the case somebody needs
                    # told about.
                    self.blocked.add(host)
                    # Remembered past this run, but only for a host there is
                    # somewhere else to ask. A reader pinned to the direct
                    # route, or a box with no provider at all, has nowhere to
                    # escalate to — so skipping its probe next run would not
                    # save a request, it would mean never reading that portal
                    # again. See BLOCK_FOR.
                    if self._pools and not self.never_proxy(url):
                        self._memory.remember(host)
                        self._memory.save()
                    if len(routes) > 1:
                        continue
                break

            # ── leaving a paid pool that did not serve the page ─────────────
            #
            # The exit it finished on is burned *here*, at the moment the pool
            # is left, and this is the only place that does it. Rotation
            # otherwise happens between attempts, so the address the last
            # attempt used would never be cooled down — and it is the one the
            # memory is still holding as current, so the next run would
            # inherit precisely the exit that had just refused this one.
            #
            # Only for a refusal, and the distinction is the same one `ROTATE`
            # draws everywhere else in this module: a 404 or a 500 came back
            # *through* that exit, so it is working and there is nothing to
            # give up on. Burning it over somebody else's bad gateway would
            # throw away a warm session to no purpose — and it is also the
            # answer no other provider would change, which is why the same
            # condition decides whether to escalate at all.
            if broke or last in ROTATE:
                self._rotate(via)
                # On to the next provider if there is one. This is the whole
                # of the fallback: a pool whose every exit refused us has said
                # something about the pool, and the next thing to vary is
                # whose addresses they are. With no fallback configured there
                # is no next route and the loop ends here by itself.
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
        where = _named(paid or DIRECT)
        if paid:
            over = "exit address" if tried == 1 else "exit addresses"
        else:
            over = "fingerprint" if tried == 1 else "fingerprints"
        if broke:
            # Says what happened rather than what was counted. A connection
            # that never opened is no evidence about a fingerprint, so the
            # free route does not pretend a ladder was climbed; the paid one
            # names how many addresses it got through, because there the
            # ladder is addresses and it really was climbed.
            spread = f" over {tried} exit addresses" if paid and tried > 1 else ""
            raise Refused(f"{url} never answered — {broke}, from {where}{spread}")
        raise Refused(
            f"{url} answered {last} under {tried} {over} tried, from {where}"
            # What the refusal itself said, which is the difference between a
            # Cloudflare challenge and a Cloudflare block — and so between
            # "a browser would get through this" and "ask from somewhere
            # else". Without it `job_events` carried the status and nothing
            # about the cause. See EVIDENCE.
            + (f" — {said}" if said else "")
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

        Raises `Refused` when no route answered, including when the reason was
        a connection that never opened rather than a status. See `_head_try`.

        ── one attempt per provider, and no ladder on any of them ────────────

        `get` spends up to `ATTEMPTS` on a page; this spends one request per
        route, and the asymmetry is deliberate. `openrent` makes hundreds of
        these in a run against `SLUG_BUDGET`, so a ladder here would multiply
        the whole budget rather than one page — and a redirect is cheap enough
        that losing one to a bad exit costs a listing's slug, not a district.
        The fallback is included on the same terms: one more request, only
        when the primary refused as well.
        """

        routes = (
            [] if self.never_proxy(url) else [one.name for one in self._pools]
        )
        if routes and host_of(url) in self.blocked:
            # Known to refuse this address, this run or an earlier one. Decided
            # here rather than inside `_head_try` so that the probe is skipped
            # *once*: the switch used to live there, which meant a host already
            # blocked got one paid lookup from the direct call and then another
            # from the escalation below — the first provider asked twice for
            # one url. On `openrent`, which makes hundreds of these a run,
            # that was hundreds of duplicate paid requests.
            self.probes_skipped += 1
            return self._head_paid(url, routes)
        try:
            status, where = self._head_try(url, via=DIRECT)
        except Refused:
            # Nothing answered. A proxy is the only other place to ask, and it
            # is the same move a refusal gets below. Not recorded in `blocked`
            # for the same reason `get` does not record it.
            if not routes:
                raise
            return self._head_paid(url, routes)
        if status in BLOCKED:
            # Remembered for the rest of the run, exactly as `get` does, so the
            # next few hundred lookups go straight through the proxy instead of
            # each paying for its own refusal first — and now past the run too,
            # so the *first* lookup of the next run does not pay for it either.
            # On `openrent` that is one saved 405 per run rather than per
            # district: the search page is served and only the lookups are
            # refused, so this is the one host whose block is learned here and
            # nowhere else.
            self.blocked.add(host_of(url))
            if routes:
                self._memory.remember(host_of(url))
                self._memory.save()
                return self._head_paid(url, routes)
        return status, where

    def _head_paid(self, url: str, routes: list[str]) -> tuple[int, str | None]:
        """One lookup through each provider in turn, until one answers.

        "Answers" means anything but a refusal: a 301 is the answer this call
        exists for, and a 404 is an answer too. Only a `BLOCKED` status or a
        connection that never opened is worth asking the next provider about,
        which is the same rule `get` escalates on.

        The last provider's refusal is the one that propagates, because it is
        the one that ends the attempt — and `_head_try` has already named the
        route in it.
        """

        for nth, via in enumerate(routes):
            last = nth == len(routes) - 1
            try:
                status, where = self._head_try(url, via=via)
            except Refused:
                if last:
                    raise
                continue
            if status in BLOCKED and not last:
                continue
            return status, where
        # Unreachable: `routes` is non-empty at every call site, so the loop
        # either returns or raises on its final pass. Here so that the type is
        # honest rather than inferred from a promise.
        raise Refused(f"{url} had no route to try")

    def _head_try(self, url: str, *, via: str) -> tuple[int, str | None]:
        """`_head_once`, with a connection that broke arriving as `Refused`.

        Every caller of `head` already handles `Refused`: `openrent` counts it
        as `slug_unreachable`, leaves the district incomplete and asks again
        next run, which is the right answer to a lookup that could not be
        made. None of them handles `CurlError`, so a timed-out tunnel ended
        the run instead of the lookup — and this is the reader that makes
        hundreds of these calls, so it is the one with the most to lose. See
        BROKEN.
        """

        try:
            return self._head_once(url, via=via)
        except CurlError as exc:
            # Re-raises anything that is our own bug rather than the network's.
            broke = _broke(exc)
            if via != DIRECT:
                # Give up on this exit for the rest of the run. Free, because
                # the handle holding that dead tunnel has just been dropped
                # anyway — and without it the next few hundred lookups would
                # each break on the same address, which is how one bad exit
                # turns a run's whole slug budget into nothing. `get` gets
                # this from the rotation inside its own attempt loop; a
                # lookup has no loop to put it in, so it moves on the way out.
                self._rotate(via)
            where = _named(via)
            raise Refused(
                f"{url} never answered — {broke}, from {where}"
            ) from exc

    def _head_once(self, url: str, *, via: str) -> tuple[int, str | None]:
        self._pause()
        body = io.BytesIO()
        headers = io.BytesIO()
        target = self._best or self.targets[0]
        slot, curl = self._handle(target, via=via)

        curl.setopt(CurlOpt.URL, url.encode())
        curl.setopt(CurlOpt.WRITEDATA, body)
        curl.setopt(CurlOpt.HEADERDATA, headers)
        curl.setopt(CurlOpt.NOBODY, 1)
        # Deliberately not followed: the redirect *is* the answer, and
        # following it would fetch the very page this call exists to avoid.
        curl.setopt(CurlOpt.FOLLOWLOCATION, 0)
        curl.setopt(CurlOpt.TIMEOUT_MS, int(self.timeout * 1000))
        curl.setopt(CurlOpt.PROXY, self._proxy_now(via).encode())

        try:
            curl.perform()
            status = _number(curl.getinfo(CurlInfo.RESPONSE_CODE))
            wire = _wire(curl)
        except Exception:
            # Metered before the handle is thrown away: see `_once`.
            self._spent(_wire(curl), via)
            self._handles.pop(slot, None)
            curl.close()
            raise
        finally:
            # A reused handle remembers these, and a bodiless request that
            # stops following redirects is not what the next `get` wants.
            # `_once` sets them too, belt and braces, because on the raising
            # path this is a no-op: the handle above it has already been
            # closed, and curl_cffi's `setopt` silently ignores a closed one.
            #
            # HEADERDATA is deliberately NOT reset to a fresh buffer here. That
            # is what the first version did, and it was the bug: the throwaway
            # was registered, then freed after the next perform, and the perform
            # after that called the header callback on the dead pointer and
            # killed the interpreter. Nothing needs resetting — `_once` sets a
            # live buffer before every perform. See CALLBACK_TARGETS.
            curl.setopt(CurlOpt.NOBODY, 0)
            curl.setopt(CurlOpt.FOLLOWLOCATION, 1)

        self._spent(wire, via)

        where = None
        for line in headers.getvalue().decode("latin1").splitlines():
            if line.lower().startswith("location:"):
                where = line.split(":", 1)[1].strip()
        return status, where

    def _spent(self, wire: int, via: str) -> None:
        """Add one request to the meters, whether or not it was served.

        Every attempt, including the refused and the broken ones: the stage
        reports `requests` and `bytes` as what this run cost, and a request
        that failed through a proxy was still paid for.

        Metered per pool as well as in total, because two providers send two
        invoices and a figure that mixed them could be reconciled against
        neither. `proxy_wire` stays the primary's alone for that reason — see
        the field's own note.
        """

        self.requests += 1
        self.wire += wire
        pool = self._pool(via)
        if pool is None:
            return
        pool.requests += 1
        pool.wire += wire
        if pool.name == PRIMARY:
            self.proxied += 1
            self.proxy_wire += wire

    def _handle(self, target: str, *, via: str) -> tuple[str, Curl]:
        """The handle for one target on one address, built if need be.

        Returns the slot as well as the handle, because the caller needs to be
        able to drop exactly this one when a transfer leaves it in an unknown
        state, and a target alone no longer identifies it.

        ── the three options that are set here and nowhere else ────────────
        #
        Everything else in this module is set on every request, on purpose and
        for a reason given at `_once`. These three cannot be:

          * `impersonate` configures the handle itself, and re-applying it per
            request would be work for nothing.
          * `COOKIEFILE` is **not idempotent**. libcurl keeps a *list* of
            files to read cookies from and each setopt appends to it, so
            saying it before every request would have the handle re-reading
            the same jar on every transfer and, worse, re-reading a jar the
            same handle had already written.
          * `COOKIEJAR` names where the cookies are flushed, which libcurl
            does on `curl_easy_cleanup` — so it has to be set before the
            first transfer and left alone until `close`.

        That is safe here only because a slot is a target *and* an address:
        the handle never changes either, so neither does its jar. See `_slot`.
        """

        slot = self._slot(target, via)
        handle = self._handles.get(slot)
        if handle is None:
            handle = Curl()
            handle.impersonate(target)
            if self.cookies:
                # From the reader's own memory, not the pool's, so that every
                # jar for one reader lands in one directory and `prune_jars`
                # sweeps all of them. The vendor and the exit are already in
                # the address — see `Pool.address` — so the paths stay
                # distinct per pool without the file having to move.
                jar = self._memory.jar(self._address(via), target)
                if jar is None:
                    # Nowhere writable, so the cookies live and die with this
                    # process. An empty COOKIEFILE still starts the engine, so
                    # a sweep of twenty districts is one session rather than
                    # twenty — just not one that outlives the run.
                    handle.setopt(CurlOpt.COOKIEFILE, b"")
                else:
                    where = str(jar).encode()
                    handle.setopt(CurlOpt.COOKIEFILE, where)
                    handle.setopt(CurlOpt.COOKIEJAR, where)
            self._handles[slot] = handle
        return slot, handle

    def close(self) -> None:
        """Let go of every connection. Safe to call twice.

        Closing a handle is also what writes its cookie jar — libcurl flushes
        on cleanup — so this is not only about connections any more. Every
        path that drops a handle therefore has to go through `close()` or
        `curl.close()` and not merely forget it, or the run's cookies go with
        it. `__exit__` and `sweep.collect`'s `finally` are what guarantee it.
        """

        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        # The last thing a run has to say about its exit addresses and the
        # hosts that refused it. `_rotate` already saves as it goes; this
        # catches a run that drew a fresh port and was served on it, which is
        # the case the next run most wants to inherit.
        #
        # Every pool, not only the reader's own file: the fallback keeps a
        # second one, and a run that was rescued by the fallback has learned
        # the single most valuable thing there is to pass on — which of that
        # vendor's addresses worked.
        self._memory.save()
        for pool in self._pools:
            if pool.memory is not None and pool.memory is not self._memory:
                pool.memory.save()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pause(self) -> None:
        """Wait a random fraction of `jitter` before asking. See JITTER.

        Drawn per request rather than per run: one delay chosen at the start
        would shift the whole burst and leave its shape untouched, which is
        the part worth removing.
        """

        if self.jitter > 0:
            time.sleep(random.uniform(0, self.jitter))

    def _once(
        self, url: str, target: str, accept: str, via: str
    ) -> Reply:
        self._pause()
        body = io.BytesIO()
        # Read now, where it used to be set and ignored. A previous `head()`
        # on this handle left the header callback installed, and curl_cffi
        # drops the reference that kept its target alive at the end of every
        # perform — so not setting it means firing that callback into freed
        # memory. See CALLBACK_TARGETS. What it carries is now worth having:
        # see EVIDENCE for why a 403's headers are the whole of the diagnosis.
        headers = io.BytesIO()
        slot, curl = self._handle(target, via=via)

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
        curl.setopt(CurlOpt.PROXY, self._proxy_now(via).encode())
        # ── the referer, and why it is not a caller's argument ─────────────
        #
        # The last page this Fetcher was served on this host. A browser
        # paginating a search carries the previous page here and a browser
        # opening a listing carries the search it was on; we carried nothing
        # at all, on every request, which is a thing no navigation does.
        #
        # Held here rather than passed in because it is a property of the
        # session, not of the call: every reader's page loop and every
        # `postcode_for` would otherwise have to thread it through, four
        # readers' worth of argument for one header. Same host only — a
        # cross-origin referer is a different decision and the only
        # cross-origin fetches here are images, which go to a CDN.
        #
        # Set on every request including the empty case, because a reused
        # handle remembers it: without the `else` branch the first page of the
        # next district would quote the last page of the previous one.
        came_from = self._seen.get(host_of(url), "")
        curl.setopt(CurlOpt.REFERER, came_from.encode() if came_from else b"")

        try:
            curl.perform()
            status = _number(curl.getinfo(CurlInfo.RESPONSE_CODE))
            wire = _wire(curl)
        except Exception:
            # Metered before it is thrown away. A tunnel the proxy refused and
            # a handshake that timed out both cost its clock and some of its
            # traffic, and `stage.set("requests", …)` reading zero after three
            # such attempts tells the wrong story about the run — which was
            # the whole reason yesterday's failures looked like no attempt at
            # all. What libcurl can still account for is taken and no more.
            self._spent(_wire(curl), via)
            # A handle that failed mid-transfer may be holding a connection in
            # an unknown state, and the next request on it would fail for a
            # reason that has nothing to do with the next request. Drop it; the
            # following call builds a fresh one.
            #
            # `curl.close()` and not merely a pop, because closing is what
            # flushes the cookie jar — a handle dropped without it loses
            # whatever the portal had issued to that session.
            self._handles.pop(slot, None)
            curl.close()
            raise

        self._spent(wire, via)
        return Reply(
            status=status,
            body=body.getvalue().decode("utf-8", errors="replace"),
            wire=wire,
            impersonated=target,
            # Only on a reply worth explaining. A served page's headers are
            # nobody's business and putting them on every Reply would mean
            # parsing 60KB of them a run to throw them all away.
            headers={} if 200 <= status < 300 else _evidence(headers.getvalue()),
        )


__all__ = [
    "ATTEMPTS", "BACKOFF", "BACKOFF_CAP", "BLOCKED", "BROKEN",
    "CALLBACK_TARGETS", "DIRECT", "DIRECT_PROBES", "EVIDENCE", "FALLBACK",
    "FALLBACK_ATTEMPTS", "IMPERSONATE", "JITTER", "KEPT_REFUSALS", "PRIMARY",
    "RETRY_AFTER_CAP", "ROTATE", "STICKY_PORTS", "TIMEOUT",
    "Fetcher", "Pool", "Refused", "Reply", "exit_ports", "host_of",
]
