"""Mint a Cloudflare clearance token with a real browser, for curl to ride.

── what this is for ─────────────────────────────────────────────────────────

One portal needs it. Zoopla answers this server's own address 403 under every
fingerprint, so `zoopla_london` reads the city through a residential exit — and
through the autumn of 2026 that exit has increasingly been answered with
`cf-mitigated: challenge` rather than a block. The two are different problems
with opposite answers, which is what `fetch.EVIDENCE` exists to tell apart: a
block wants a different address, and a challenge wants a browser, because a
browser is the only thing that can pass one.

── why the browser is not the transport ─────────────────────────────────────

The obvious move is to fetch the page with Playwright and be done. It is also
about forty times the traffic, on a reader that runs every six minutes through
the working day, for a page curl can fetch perfectly well the rest of the time.

So the browser never fetches anything the scraper wants. It opens the page
once, lets Cloudflare satisfy itself, and writes the cookies it was given into
the jar the scraper's own handle already reads — see `Fetcher._handle`, which
sets libcurl's COOKIEFILE and COOKIEJAR to exactly this path. From then on curl
presents a token a browser earned, which is the whole trick, and the browser
goes away.

── the one thing that can make all of this worthless ────────────────────────

`cf_clearance` is bound to the user agent that earned it, and Cloudflare scores
the TLS handshake beside it. This module's `TARGET` names the impersonation the
scraper will present the token under — but the browser minting it is a real
Chromium whose own version is whatever Playwright installed, and the repo
deliberately has no separate User-Agent setting anywhere precisely because
`impersonate()` moves the agent and the handshake together (see the note at
fetch.py:230). Overriding the browser's agent to match would move one of the
two and leave the rest, which is the failure that note warns about.

So the match is not asserted here, it is *measured*: after writing the jar the
minter makes one real curl request through the same exit with the same jar, and
only a 200 counts as a token minted. That is what `clearance_minted` means on
the admin console's Host tab, and it is why a mint rate that sits low is the
signal to check the Chromium version against `TARGET` rather than to blame the
pool.

── how the browser and the readers agree on an address ──────────────────────

Through the same memory file, and in one direction: this asks
`Session.pick` which exit to use, and on success marks it served and writes it
out — so the next `zoopla_london` run resumes exactly the exit the token was
minted on, because a proven port is the one `pick` keeps. That is also why a
failed mint deliberately does not save: a port nothing was served on must not
be handed to the reader as a session.

It is not airtight, and in the same way the jar below is not: a scrape already
in flight holds its own `Session` object and saves it at close, so it can
overwrite the exit this chose. The consequence is one run presenting a token
minted for a different peer, which Cloudflare will refuse, and the run after it
being right again.

── what it does not do ──────────────────────────────────────────────────────

It does not lock. `zoopla_london` runs every six minutes and this every
twenty-five, so the two will sometimes overlap, and libcurl flushes its own
cookies over this file when a handle closes. The jar is written atomically so
a reader never sees half of one, but a scrape that closes its handle moments
after a mint can still overwrite a fresh token with its own older set. The
cost of that is one run going unprotected until the next mint, which is a
quarter of an hour; the cost of a lock between two systemd units that must not
block each other is worse. Said here rather than discovered later.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg

from worker import store
from worker.obs import Run, host
from worker.sources import session as session_memory
from worker.sources.fetch import IMPERSONATE, PRIMARY, exit_ports
from worker.sources.zoopla import BASE, NEWEST_FIRST, SOURCE_KEY

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

#: The page the token has to be good for, which is the page that gets
#: challenged: the one `zoopla_london` reads every six minutes. Minting against
#: a cheaper page would be minting against a different Cloudflare rule.
MINT_URL = f"{BASE}/to-rent/property/london/?results_sort={NEWEST_FIRST}"

#: The impersonation the scraper will present this token under, and therefore
#: the jar to write. First of `IMPERSONATE` because that is the one the ladder
#: starts from — see `Fetcher._ladder`. If this and the installed Chromium
#: disagree about which Chrome they are, the verification below is what says so.
TARGET = IMPERSONATE[0]

#: Whose memory to read. The *source* key, not the job name: `zoopla` and
#: `zoopla_london` are two jobs on one key and one sticky exit, and
#: `Fetcher.from_env(session=portal.key)` is what the readers pass. Getting
#: this wrong would mint a token for an address nothing reads from.
SESSION = SOURCE_KEY

#: How long to give the whole thing. Past this the run is degraded rather than
#: held open: a oneshot that outlives its timer is a timer firing behind
#: itself, and the next mint is twenty-five minutes away in any case.
TIMEOUT_MS = 60_000

#: Cookies worth keeping out of whatever the page set. `cf_clearance` is the
#: token; `__cf_bm` is the bot-management session it travels with; `_cfuvid`
#: rides along. Everything else a Zoopla page sets is analytics, and a jar is
#: presented on every request this reader makes — so it carries what Cloudflare
#: needs and nothing that merely identifies us more precisely.
KEEP = ("cf_clearance", "__cf_bm", "_cfuvid")

#: How far back to look for evidence that the reader is actually being
#: challenged. See `mint`, which will not launch a browser without it.
#:
#: Thirty minutes, which is a little over the timer's own twenty-five: every
#: challenge since the previous mint decision is in scope, so a challenge
#: cannot land in the gap between two runs and be seen by neither.
CHALLENGE_WINDOW_MINUTES = 30

#: Statuses Cloudflare serves an interactive challenge under. A page that
#: arrives any other way was not challenged, and the absence of a token on it
#: is not a failure to mint one — see `mint`.
#:
#: 403 is what Zoopla answers this pool with and carries `cf-mitigated`; 503 is
#: the older JS interstitial, kept because the edge still serves it to some
#: exits and it is the same situation with a different number on it.
CHALLENGED = (403, 503)


def mint(conn: Conn, run: Run, *, dry_run: bool = False) -> str:
    """Open Zoopla in a browser and leave its cookies where curl will find them.

    Does nothing at all unless the reader is actually being challenged. See
    `store.challenged_recently` and the note below on why the browser is no
    longer launched on the timer alone.

    Returns the run status. Never raises for a portal's behaviour — a
    challenge that does not clear is a degraded run, not a crash — but does
    raise if the browser itself cannot start, because that is a deployment
    fault and silence about it would mean a tab of zeroes and no reason.
    """

    with run.stage("clearance", source_key=SESSION) as stage:
        # ── the browser is launched on evidence, not on the clock ──────────
        #
        # This used to mint on every timer tick, to keep a token warm ahead of
        # the reader needing one. Measured on 2 November 2026 that was a
        # browser every twenty-five minutes on a 4 GB box, against a
        # `zoopla_london` that was not being challenged at all: every run since
        # 08:05 reported `no cf_clearance in the browser's cookies`, which with
        # green reads means Cloudflare was serving the page without a challenge
        # and there was no token to collect.
        #
        # So the question is asked of the reader's own refusals rather than
        # guessed at. `refusal_detail` records a challenge the moment one is
        # served — before the reader starts losing pages, because it is written
        # even when another exit then carried the run — so this is reactive
        # without being late.
        #
        # The cost is the window: a challenge that first appears one minute
        # after a tick waits out the rest of the twenty-five before a token is
        # minted for it, and `zoopla_london` spends that time climbing its own
        # ladder. That is the trade — a quarter hour of the reader's existing
        # fallbacks against a browser that was running for nothing.
        # The gate is on the *schedule*, not on the job. A person who types
        # `python -m worker clearance` is asking for a browser on purpose —
        # usually to check the installed Chromium against TARGET, which the
        # module note calls the answer to a mint rate that sits low, and which
        # there would otherwise be no way to do on a day Zoopla is behaving.
        # The timer passes `--trigger schedule`; nothing else does.
        challenged = store.challenged_recently(
            conn, source_key=SESSION, within_minutes=CHALLENGE_WINDOW_MINUTES
        )
        stage.set("challenged", challenged)
        if not challenged and run.trigger == "schedule":
            # "ok" and an info line, not a skip and not a degradation. Nothing
            # was lost and nothing needs doing: being served the page is the
            # outcome this whole module exists to restore.
            stage.log(
                "info",
                f"{SESSION} has not been challenged in the last "
                f"{CHALLENGE_WINDOW_MINUTES} minutes; no token needed",
            )
            return "ok"

        memory = session_memory.Session.load(SESSION)
        proxy = (os.environ.get("SCRAPE_PROXY") or "").strip()
        ports = exit_ports(proxy) if proxy else ()
        port = memory.pick(ports) if ports else None
        if proxy and port is None:
            # A proxy is configured but names no port this can sit on, which
            # means the sticky range is not what `exit_ports` recognises. Minting
            # through a different address than the scraper uses is worse than
            # not minting: the token would be bound to a peer nothing reads from.
            stage.degrade("SCRAPE_PROXY has no sticky port to mint on", recoverable=False)
            return "degraded"

        where = _with_port(proxy, port) if proxy and port is not None else ""
        address = f"{PRIMARY}-{port}" if port is not None else PRIMARY
        jar = memory.jar(address, TARGET)
        if jar is None:
            stage.degrade("no writable state directory for the cookie jar", recoverable=False)
            return "degraded"

        stage.set("exit_port", port)
        stage.set("target", TARGET)
        if dry_run:
            stage.log("info", f"would mint {MINT_URL} through {address}")
            return "ok"

        began = time.monotonic()
        try:
            cookies, served = _open_and_collect(where)
        finally:
            # Recorded whatever happened, because a browser that died having
            # eaten the box is exactly the case the Host tab is watching for,
            # and it is also the case that returns no cookies.
            stage.set("browser_seconds", round(time.monotonic() - began, 2))
            peak = host.peak_child_bytes()
            if peak is not None:
                stage.set("browser_peak_bytes", peak)

        stage.set("page_status", served)
        stage.set("cookies_seen", len(cookies))

        token = next((one for one in cookies if one.get("name") == "cf_clearance"), None)
        if token is None:
            # Two different things used to land here and both were reported as
            # a degradation, which is why `clearance` sat amber for a whole day
            # with nothing wrong: Cloudflare serving a challenge the browser
            # did not clear, and Cloudflare serving the page with no challenge
            # at all. The status the navigation itself answered is what tells
            # them apart — see CHALLENGED — and it is recorded either way, so
            # the journal no longer needs a guess.
            stage.set("clearance_minted", 0)
            if served not in CHALLENGED:
                # No challenge to pass, so no token exists to collect. The
                # reader was challenged within the window or this run would
                # have stopped at the gate above, so what happened in between
                # is Cloudflare relenting — which is the good outcome, arrived
                # at without us.
                stage.log(
                    "info",
                    f"{MINT_URL} answered {served} with no challenge; "
                    f"nothing to mint and nothing to fix",
                )
                return "ok"
            stage.degrade(
                f"no cf_clearance in the browser's cookies; {MINT_URL} answered "
                f"{served} and the browser did not clear the challenge",
                recoverable=True,
            )
            return "degraded"

        _write_jar(jar, cookies)
        stage.count("cookies_written", len(_keepable(cookies)))

        # The measurement the module note is about: does curl actually get
        # through with what the browser earned. Only this counts as minted.
        status = _verify(where, jar)
        stage.set("verify_status", status)
        stage.set("clearance_minted", 1 if status == 200 else 0)
        if status != 200:
            stage.degrade(
                f"minted a token curl could not use — {MINT_URL} answered {status} "
                f"under {TARGET}; check the installed Chromium against TARGET",
                recoverable=True,
            )
            return "degraded"

        memory.warmed(port)
        memory.save()
        stage.log("info", f"cf_clearance minted through {address} and accepted under {TARGET}")
        return "ok"


def _open_and_collect(proxy: str) -> tuple[list[dict[str, Any]], int]:
    """Launch, navigate, wait for the challenge, return the cookies and status.

    The status is the one the *navigation* answered, which is the interstitial's
    own and not the page that replaced it: Playwright hands back the response to
    the first main-frame request, and a challenge that clears reloads behind it.
    That is exactly what is wanted here — whether a challenge was served at all
    — and it is why the caller can read a missing token two ways. See CHALLENGED.

    Zero when the navigation answered nothing to read a status from, which
    Playwright reports as a null response. Not treated as a challenge, because
    guessing "challenged" from an absence is how this reported a problem it had
    no evidence for in the first place.

    Imported here and not at module scope for the reason every other optional
    import in this package is: the delivery host installs neither the scraping
    extra nor the browser one, and `import worker.pipeline.run` must work there.
    """

    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = play.chromium.launch(
            # Headless, and the sandbox left on. A worker running as its own
            # unprivileged user has no need of --no-sandbox, which is the flag
            # everybody copies and nobody needs outside a container.
            headless=True,
            args=[
                # The GPU process is pure cost on a box with no display, and
                # on 4 GB it is cost worth not paying.
                "--disable-gpu",
                # One tab, no extensions, no first-run machinery.
                "--disable-extensions",
                "--no-first-run",
            ],
            proxy=_proxy_for_playwright(proxy),
            timeout=TIMEOUT_MS,
        )
        try:
            page = browser.new_page()
            answer = page.goto(MINT_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            served = answer.status if answer is not None else 0
            # The interstitial replaces itself when it is satisfied, so what
            # is being waited for is the real page arriving rather than a
            # fixed sleep. `networkidle` rather than a selector because a
            # selector would couple this to Zoopla's markup, which is the one
            # thing in this project that changes without notice.
            page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            return list(page.context.cookies()), served
        finally:
            browser.close()


def _verify(proxy: str, jar: Path) -> int:
    """Fetch the same page with curl, through the same exit, with that jar.

    The status it answers is the only honest evidence that the token is worth
    anything — see the module note on why it cannot be assumed.
    """

    from curl_cffi import Curl, CurlInfo, CurlOpt

    handle = Curl()
    handle.impersonate(TARGET)
    try:
        handle.setopt(CurlOpt.URL, MINT_URL.encode())
        # Read, and deliberately not written back: libcurl flushes COOKIEJAR on
        # cleanup, and this request must not be what rewrites the file it was
        # given. Only COOKIEFILE here.
        handle.setopt(CurlOpt.COOKIEFILE, str(jar).encode())
        if proxy:
            handle.setopt(CurlOpt.PROXY, proxy.encode())
        handle.setopt(CurlOpt.NOBODY, 1)
        handle.perform()
        # Narrowed rather than cast: `getinfo` is typed as returning any of
        # bytes, a number or a list, and `int()` over the union is a crash
        # waiting for the one handle that answers something else. Anything that
        # is not a number is "no answer", which is what the caller degrades on.
        code = handle.getinfo(CurlInfo.RESPONSE_CODE)
        return int(code) if isinstance(code, (int, float)) else 0
    except Exception:
        # A tunnel that would not open says nothing about the token, and this
        # function's whole job is to report a status rather than to decide
        # anything. Zero is "no answer", which is what the caller degrades on.
        return 0
    finally:
        handle.close()


def _keepable(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [one for one in cookies if one.get("name") in KEEP]


def _write_jar(jar: Path, cookies: list[dict[str, Any]]) -> None:
    """Write the cookies where libcurl will read them, atomically.

    Netscape format, which is what COOKIEFILE wants: domain, whether subdomains
    are included, path, secure, expiry, name, value — tab separated. The
    `#HttpOnly_` prefix is libcurl's own convention for a cookie that may not be
    read by script, and `cf_clearance` is one, so without the prefix libcurl
    reads the line as a comment and the whole exercise is silent and useless.

    Replaced rather than written in place for the reason `Session.save` is: the
    thing that reads this is another process, and half a jar is worse than none.
    """

    lines = ["# Netscape HTTP Cookie File", "# Written by worker.sources.clearance"]
    for one in _keepable(cookies):
        domain = str(one.get("domain") or "")
        if not domain:
            continue
        # A leading dot is how the file format spells "and its subdomains",
        # which is also what Playwright's own flag means.
        subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if one.get("secure") else "FALSE"
        # A session cookie has no expiry and Playwright reports -1 for it.
        # Zero is how the format spells the same thing.
        expires = int(one.get("expires") or 0)
        prefix = "#HttpOnly_" if one.get("httpOnly") else ""
        lines.append(
            "\t".join(
                (
                    f"{prefix}{domain}",
                    subdomains,
                    str(one.get("path") or "/"),
                    secure,
                    str(max(expires, 0)),
                    str(one.get("name") or ""),
                    str(one.get("value") or ""),
                )
            )
        )
    temporary = jar.with_name(jar.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, jar)


def _proxy_for_playwright(proxy: str) -> dict[str, str] | None:
    """Playwright wants the credentials apart from the server; libcurl does not.

    The configured url carries them inline — `http://user:pass@host:10234` —
    and handing that whole string to `launch(proxy=...)` authenticates with
    nothing at all, silently, because Chromium parses the url and drops the
    userinfo. So it is split here rather than discovered as a pool of 407s.
    """

    if not proxy:
        return None
    parts = urlsplit(proxy)
    host_port = parts.hostname or ""
    if parts.port:
        host_port = f"{host_port}:{parts.port}"
    named = {"server": f"{parts.scheme or 'http'}://{host_port}"}
    if parts.username:
        named["username"] = parts.username
    if parts.password:
        named["password"] = parts.password
    return named


def _with_port(proxy: str, port: int) -> str:
    """The configured proxy on the exit the scraper's memory is holding."""

    return f"{proxy.rpartition(':')[0]}:{port}"


__all__ = ["KEEP", "MINT_URL", "SESSION", "TARGET", "TIMEOUT_MS", "mint"]
