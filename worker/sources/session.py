"""What a scraper remembers between runs.

Everything here exists because of one fact about the deployment: the portal
readers are `Type=oneshot` systemd units. A run is a process that starts, asks
for a page and exits, so anything a `Fetcher` learned — which exit address
served it, which cookies the portal issued, which host refuses this server —
died with it. Five minutes later the next run started knowing nothing.

That is not a small loss, and on Zoopla it is most of the problem.

── what the portal actually does with a returning visitor ──────────────────

Measured against the live London search page on 12 October 2026, through one
cookie jar file and two *separate* libcurl handles:

    run 1, cold, no jar         4 x Set-Cookie
    run 2, same jar on disk     1 x Set-Cookie

The four are `ajs_anonymous_id`, `_needs_refresh`, `_cfuvid` and `__cf_bm`.
The one is a refreshed `__cf_bm` — Cloudflare recognised the session and did
not re-mint the other three. So the jar is not a nicety: presenting it is the
difference between arriving as a visitor the site has seen and arriving as one
it has not, and that distinction survives the process boundary.

`__cf_bm` is Cloudflare's bot-management token and it carries a **1800 second**
expiry (measured: issued 1791808552, expires 1791810352). The readers run on a
five-minute timer, so one warm token covers about six consecutive runs — which
is only reachable if something outlives the process, hence this file.

── why the sticky exit has to be remembered too, and not drawn afresh ──────

`Fetcher` used to choose its exit port as `os.getpid() % 501`. Across oneshot
runs the pid is effectively arbitrary, so every five minutes the reader asked
Zoopla for a page from a residential address it had never used before, with no
cookies. That is the worst available combination for a bot score: a brand-new
peer with no history, every time, for ever.

The comment it replaced was not wrong about what it was fixing — one configured
port meant one address carrying every proxied request this server ever made,
and spreading that load was the right call. The error was spreading it *per
run* rather than *per reader*. So:

  * the exit is remembered and reused for as long as it is being served, which
    is what makes the cookie above worth having;
  * it is remembered under a name, one per reader, so the three readers still
    do not share an address and a burned exit still costs one of them;
  * a refused exit goes into `cooldown` and is not drawn again for an hour, so
    the rotation stops re-offering the address that just failed.

── why a file and not the database ─────────────────────────────────────────

`fetch` has no database dependency and its tests import it without one. Giving
the transport a `Conn` to carry would couple the one module that must work on
a laptop, on the Windows desk and on the server to the one thing that is not
present in all three. A file in the state directory is enough: the state is
per host anyway — an exit address and a cookie are facts about *this* machine's
session, not about the account.

── where it lives ─────────────────────────────────────────────────────────

`$STATE_DIRECTORY`, which systemd sets from `StateDirectory=` in the unit, so
on the server this needs no configuration at all. The unit is
`ProtectSystem=strict`, `ProtectHome=true` and `PrivateTmp=true`, which means
nothing else on that box is writable — `/tmp` least of all, since it is wiped
between invocations and so would be a cross-run store that never stores
anything across runs.

`SCRAPE_STATE_DIR` overrides it, and off the server it falls back to the
user's cache directory, which is what the Windows desk and a laptop want.

── nothing here may ever raise ────────────────────────────────────────────

A read-only disk, a full one, a half-written file from a run that was killed:
every one of those is a reason to scrape *without* memory, and none of them is
a reason not to scrape. So every path either returns a usable value or returns
nothing, and a corrupt file is treated exactly like an absent one.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

#: How long a refused exit address is left alone, in seconds.
#:
#: An hour, and the reasoning is the same shape as `fetch.ATTEMPTS`: there is no
#: measured per-exit recovery time to put a number on, so what is being chosen
#: is a cost. Too short and the rotation re-offers the address that just
#: refused us, which is the bug this replaces. Too long and the pool shrinks
#: for no reason. An hour is longer than `__cf_bm` lives, so an address coming
#: off cooldown is starting a fresh session rather than resuming a scored one.
COOLDOWN = 3600.0

#: How long one exit address may serve a reader before it is retired, seconds.
#:
#: Not needed for correctness — a dead sticky session answers with a refusal
#: or a broken tunnel, and `burn` already handles both. It is here for the
#: concern the pid-offset was written for: at twelve pages an hour, six hours
#: is about seventy pages from one house, which is a day's browsing for a
#: person, and it bounds the day's traffic to roughly four addresses rather
#: than one. Raising it buys warmer sessions; lowering it spreads the load.
MAX_AGE = 6 * 3600.0

#: How long "this host refuses this machine's own address" is believed.
#:
#: Zoopla has answered this server 403 under every fingerprint since September
#: and OpenRent 405, so the direct probe that discovers it is a request whose
#: answer is already known — and it is not a free one. It costs a page we
#: could have spent on the proxy, and it tells the portal that the datacentre
#: address behind it is still scraping.
#:
#: Six hours rather than for ever, because the property it replaces is worth
#: keeping: a portal that stops blocking us used to go back to being free by
#: itself on the next run, with nothing to reconfigure. Now it does so by
#: itself within the day.
BLOCK_FOR = 6 * 3600.0

#: Most refused exits to carry at once. Three attempts a run against twelve
#: runs an hour is at most thirty-six ports an hour, and `COOLDOWN` is an
#: hour, so the honest ceiling is around that. This is the guard against the
#: file growing without bound if either number ever changes — the entries
#: expiring soonest are the ones dropped, because they are the ones about to
#: stop mattering anyway.
REMEMBERED = 128

#: Cookie jars untouched for longer than this are deleted on load.
#:
#: A jar whose `__cf_bm` expired half a day ago is not a warm session, it is a
#: file. Pruning matters because the jar is keyed per exit address and there
#: are 501 of those: without this, a month of rotations leaves a directory of
#: dead sessions, and `MAX_AGE` guarantees we will never want the old ones.
JAR_FOR = 24 * 3600.0

#: Anything that is not one of these is replaced in a jar's filename. The
#: parts are a reader name, an exit address and an impersonation target, none
#: of which should contain a path separator — but one of them comes from
#: `SCRAPE_PROXY` by way of a port number, so this is belt and braces rather
#: than decoration.
UNSAFE = re.compile(r"[^a-z0-9]+")


def state_dir() -> Path | None:
    """Where cross-run state goes, created if need be. None when nowhere is.

    `SCRAPE_STATE_DIR` first so a run by hand can be pointed at a scratch
    directory; `STATE_DIRECTORY` next, which systemd sets and which is the
    only writable path on the server; the user's cache last, for the Windows
    desk and a laptop.
    """

    for raw in (
        os.environ.get("SCRAPE_STATE_DIR"),
        os.environ.get("STATE_DIRECTORY"),
    ):
        if raw and raw.strip():
            # systemd passes a colon-separated list when the unit names more
            # than one. We only ever want the first.
            return _usable(Path(raw.split(os.pathsep)[0].strip()))
    home = os.environ.get("XDG_CACHE_HOME") or ""
    base = Path(home) if home.strip() else Path.home() / ".cache"
    return _usable(base / "london-home-finder")


def _usable(path: Path) -> Path | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path if os.access(path, os.W_OK) else None


@dataclass
class Session:
    """One reader's memory of the exits, the cookies and the refusals.

    Named rather than global: the three readers that go through the proxy must
    not converge on one exit address, which is the whole reason the old code
    offset by pid. One file each keeps them independent while still letting
    each one be sticky — the property the offset could not provide.
    """

    #: Which reader this belongs to. Becomes part of the filename.
    name: str
    #: Where it is stored, or None when nothing is writable. Everything below
    #: still works in that case; it just does not survive the process.
    path: Path | None = None
    #: The exit port in use, or None when there is no sticky range to use.
    port: int | None = None
    #: When `port` was first drawn. Against `MAX_AGE`.
    since: float = 0.0
    #: Exit port → the moment it may be drawn again.
    cooldown: dict[int, float] = field(default_factory=dict)
    #: Host → the moment its direct address is worth probing again.
    blocked: dict[str, float] = field(default_factory=dict)
    #: Whether anything changed and `save` has work to do.
    dirty: bool = field(default=False, init=False)

    @classmethod
    def load(cls, name: str) -> Session:
        """This reader's state off disk. An empty one when there is none."""

        directory = state_dir()
        if directory is None:
            return cls(name=name)
        path = directory / f"exit-{_safe(name)}.json"
        session = cls(name=name, path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Absent, unreadable, or half-written by a run that was killed.
            # All three mean the same thing: start again, say nothing.
            return session
        if not isinstance(raw, dict):
            return session
        session.port = raw.get("port") if isinstance(raw.get("port"), int) else None
        session.since = _float(raw.get("since"))
        session.cooldown = {
            int(str(key)): _float(value)
            for key, value in _mapping(raw.get("cooldown")).items()
            if str(key).isdigit()
        }
        session.blocked = {
            str(key).lower(): _float(value)
            for key, value in _mapping(raw.get("blocked")).items()
        }
        session.expire()
        return session

    # ── the exit address ───────────────────────────────────────────────────

    def pick(self, ports: tuple[int, ...]) -> int | None:
        """The exit port to use. The remembered one whenever it still serves.

        "Still serves" is three conditions, and the third is the one worth
        naming: a port that is not in `ports` at all is a leftover from a
        different `SCRAPE_PROXY`, and reconnecting to it would send the run at
        a port on the new proxy that nobody is listening on.
        """

        if not ports:
            return None
        now = time.time()
        self.expire(now)
        if (
            self.port in ports
            and self.port not in self.cooldown
            and now - self.since < MAX_AGE
        ):
            return self.port
        return self._fresh(ports, now)

    def burn(self, ports: tuple[int, ...]) -> int | None:
        """Give up on the current exit and return the next one to try.

        The abandoned port goes on cooldown, which is the half the old
        rotation was missing: it advanced to the next index and so could — and
        on a 501-port range eventually would — come back round to an address
        it had already been refused by.
        """

        now = time.time()
        if self.port is not None:
            self.cooldown[self.port] = now + COOLDOWN
            self.dirty = True
        self._trim()
        if not ports:
            self.port = None
            return None
        return self._fresh(ports, now)

    def _fresh(self, ports: tuple[int, ...], now: float) -> int:
        """Draw an exit nobody has just been refused by, and not this one.

        Random rather than the next index along. Sequential draws make two
        readers that happen to burn an exit at the same time walk the range in
        step, and the point of the per-reader file is that they do not.

        The port being left is excluded explicitly rather than relied on being
        on cooldown, because only one of the two callers puts it there. `burn`
        does; `pick` retiring an address for age does not — the address is not
        suspected of anything, it has just carried enough — and without this a
        retirement could draw the very port it was retiring. On a ten-port
        range that is a one-in-ten chance of the move not happening, which is
        exactly the kind of thing that passes nine test runs out of ten.

        Then two fallbacks, narrowest first, because standing still is the one
        answer that is never right: anything not the current port, and failing
        that the whole range. A reader with every exit on cooldown should ask
        again from the least-recently-refused one rather than decline to work
        — an hour of that would be an hour of announcing nothing, and the
        refusals may never have been about us.
        """

        free = [
            one for one in ports if one not in self.cooldown and one != self.port
        ]
        if not free:
            free = [one for one in ports if one != self.port] or list(ports)
        self.port = random.choice(free)
        self.since = now
        self.dirty = True
        return self.port

    # ── the host that will not serve this machine ──────────────────────────

    def refuses(self, host: str) -> bool:
        """Whether this host is known to refuse this machine's own address."""

        return host.lower() in self.blocked

    def remember(self, host: str) -> None:
        """Record that this host refused the direct route. See BLOCK_FOR."""

        self.blocked[host.lower()] = time.time() + BLOCK_FOR
        self.dirty = True

    def forget(self, host: str) -> None:
        """Record that it served us after all, so the next run probes again."""

        if self.blocked.pop(host.lower(), None) is not None:
            self.dirty = True

    # ── the cookies ────────────────────────────────────────────────────────

    def jar(self, address: str, target: str) -> Path | None:
        """The cookie jar for one exit address under one browser.

        Keyed by **both**, and neither half is optional:

          * by address, because `__cf_bm` is issued against the peer that
            asked for it. Replaying one from a different exit is not a warm
            session, it is a cookie that disagrees with the connection — which
            is a worse signal than having no cookie at all.
          * by target, because Cloudflare's `cf_clearance` is bound to the
            user agent that earned it, and `impersonate` moves the user agent
            and the TLS fingerprint together. A jar shared across targets
            would hand one browser's token to another.

        One file per handle, which also means two handles never write the same
        jar and so cannot lose each other's cookies on close.
        """

        if self.path is None:
            return None
        jars = self.path.parent / "cookies"
        try:
            jars.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return jars / f"{_safe(f'{self.name}-{address}-{target}')}.txt"

    def prune_jars(self, *, older_than: float = JAR_FOR) -> int:
        """Delete cookie jars nothing will want again. Returns how many."""

        if self.path is None:
            return 0
        jars = self.path.parent / "cookies"
        cutoff = time.time() - older_than
        gone = 0
        try:
            found = list(jars.glob("*.txt"))
        except OSError:
            return 0
        for one in found:
            try:
                if one.stat().st_mtime < cutoff:
                    one.unlink()
                    gone += 1
            except OSError:
                continue
        return gone

    # ── bookkeeping ────────────────────────────────────────────────────────

    def expire(self, now: float | None = None) -> None:
        """Drop everything whose time has passed."""

        when = time.time() if now is None else now
        for port in [one for one, until in self.cooldown.items() if until <= when]:
            del self.cooldown[port]
            self.dirty = True
        for host in [one for one, until in self.blocked.items() if until <= when]:
            del self.blocked[host]
            self.dirty = True

    def _trim(self) -> None:
        """Keep `cooldown` bounded. See REMEMBERED."""

        if len(self.cooldown) <= REMEMBERED:
            return
        order = sorted(self.cooldown.items(), key=lambda pair: pair[1])
        self.cooldown = dict(order[-REMEMBERED:])
        self.dirty = True

    def save(self) -> bool:
        """Write it out if anything changed. Never raises; may do nothing.

        Written to a neighbouring file and moved into place, because the thing
        that reads it is the next run and a half-written file would be its
        whole memory. `os.replace` is atomic on both platforms this runs on.

        There is no lock. Two runs of one reader cannot overlap — the units
        are oneshot and the pipeline already records `skipped_locked` for a
        run that found one in progress — and two *different* readers have two
        different files, which is what `name` is for.
        """

        if self.path is None or not self.dirty:
            return False
        payload = {
            "port": self.port,
            "since": round(self.since, 3),
            "cooldown": {str(key): round(value, 3) for key, value in self.cooldown.items()},
            "blocked": {key: round(value, 3) for key, value in self.blocked.items()},
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            return False
        self.dirty = False
        return True


def _safe(name: str) -> str:
    return UNSAFE.sub("-", name.lower()).strip("-") or "default"


def _float(raw: object) -> float:
    return float(raw) if isinstance(raw, (int, float)) else 0.0


def _mapping(raw: object) -> dict[object, object]:
    return raw if isinstance(raw, dict) else {}


__all__ = [
    "BLOCK_FOR", "COOLDOWN", "JAR_FOR", "MAX_AGE", "REMEMBERED",
    "Session", "state_dir",
]
