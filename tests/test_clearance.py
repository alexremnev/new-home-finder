"""The minter's two halves that can be wrong silently.

The browser itself is not tested here and cannot usefully be: launching
Chromium is the one part of this that either works on the server or does not,
and a mock of it would only assert that the mock was called. What *can* be
wrong without anything looking wrong is the pair of translations either side —
a cookie file libcurl reads as comments, and a proxy url Chromium authenticates
to with nothing at all. Both fail silently and both look like Cloudflare
refusing us.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.obs import Run
from worker.sources import clearance


def _cookie(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "cf_clearance",
        "value": "token-value",
        "domain": ".zoopla.co.uk",
        "path": "/",
        "expires": 1_800_000_000,
        "httpOnly": True,
        "secure": True,
    }
    base.update(over)
    return base


def test_an_httponly_cookie_is_written_so_libcurl_does_not_read_it_as_a_comment(
    tmp_path: Path,
) -> None:
    # The whole exercise turns on this one prefix. `cf_clearance` is HttpOnly,
    # and a Netscape jar spells that with a `#HttpOnly_` marker before the
    # domain — so a line written without it begins with a `#` and libcurl skips
    # it as a comment. The token would be minted, written, and never presented,
    # and nothing anywhere would say so.
    jar = tmp_path / "jar.txt"
    clearance._write_jar(jar, [_cookie()])

    lines = [one for one in jar.read_text(encoding="utf-8").splitlines() if "cf_clearance" in one]
    assert len(lines) == 1
    assert lines[0].startswith("#HttpOnly_.zoopla.co.uk\t")
    fields = lines[0].split("\t")
    # domain, subdomains, path, secure, expiry, name, value — seven, in that
    # order. A field out of place is a jar libcurl rejects without comment.
    assert fields[1] == "TRUE"
    assert fields[2] == "/"
    assert fields[3] == "TRUE"
    assert fields[4] == "1800000000"
    assert fields[5] == "cf_clearance"
    assert fields[6] == "token-value"


def test_only_the_cookies_cloudflare_needs_are_kept(tmp_path: Path) -> None:
    # A jar is presented on every request this reader makes, so what goes in it
    # is what Cloudflare needs and nothing that merely identifies us better.
    jar = tmp_path / "jar.txt"
    clearance._write_jar(
        jar,
        [
            _cookie(),
            _cookie(name="__cf_bm", httpOnly=True),
            _cookie(name="ajs_anonymous_id", httpOnly=False),
        ],
    )

    body = jar.read_text(encoding="utf-8")
    assert "cf_clearance" in body
    assert "__cf_bm" in body
    assert "ajs_anonymous_id" not in body


def test_a_session_cookie_gets_a_zero_expiry_rather_than_playwrights_minus_one(
    tmp_path: Path,
) -> None:
    # Playwright reports -1 for a cookie with no expiry; the file format spells
    # the same thing as 0. Written through as -1 it is a date in 1969, which
    # libcurl reads as already expired.
    jar = tmp_path / "jar.txt"
    clearance._write_jar(jar, [_cookie(expires=-1)])

    lines = jar.read_text(encoding="utf-8").splitlines()
    line = next(one for one in lines if "cf_clearance" in one)
    assert line.split("\t")[4] == "0"


def test_the_jar_is_replaced_rather_than_written_in_place(tmp_path: Path) -> None:
    # The thing that reads this is another process — see the module note on why
    # there is no lock — and half a jar is worse than none.
    jar = tmp_path / "jar.txt"
    jar.write_text("# stale\n", encoding="utf-8")
    clearance._write_jar(jar, [_cookie(value="fresh")])

    assert "fresh" in jar.read_text(encoding="utf-8")
    assert not (tmp_path / "jar.txt.tmp").exists()


def test_credentials_are_split_out_because_chromium_drops_them_from_the_url() -> None:
    # Handing Playwright the whole `http://user:pass@host:10234` authenticates
    # with nothing, silently, because Chromium parses the url and discards the
    # userinfo. The symptom is a pool of 407s that reads exactly like a proxy
    # provider having a bad day.
    named = clearance._proxy_for_playwright("http://user:secret@gw.example.com:10234")

    assert named == {
        "server": "http://gw.example.com:10234",
        "username": "user",
        "password": "secret",
    }


def test_a_proxy_without_credentials_carries_none() -> None:
    assert clearance._proxy_for_playwright("http://gw.example.com:10234") == {
        "server": "http://gw.example.com:10234"
    }
    # No proxy at all is not an empty dict: Playwright treats `proxy={}` as a
    # configuration and `proxy=None` as "go direct".
    assert clearance._proxy_for_playwright("") is None


def test_the_mint_goes_out_through_the_exit_the_readers_memory_is_holding() -> None:
    # Minting through a different address than the scraper uses is worse than
    # not minting: `cf_clearance` is issued against the peer that asked for it,
    # so a token earned on another exit is a cookie that disagrees with the
    # connection presenting it.
    assert clearance._with_port("http://user:pass@gw.example.com:10000", 10234) == (
        "http://user:pass@gw.example.com:10234"
    )


def test_it_mints_against_the_page_that_is_actually_challenged() -> None:
    # Not a cheaper page: a token is good for the Cloudflare rule it was earned
    # under, and the rule that refuses us is the one on the region search page
    # `zoopla_london` reads every six minutes.
    assert clearance.MINT_URL == (
        "https://www.zoopla.co.uk/to-rent/property/london/?results_sort=newest_listings"
    )
    # The source key, not the job name — two jobs share one sticky exit, and
    # the readers pass `Fetcher.from_env(session=portal.key)`.
    assert clearance.SESSION == "zoopla"


# ── when the browser runs at all ────────────────────────────────────────────
#
# Added after 2 November 2026, when `clearance` reported `degraded` on every
# one of a day's runs while `zoopla_london` was entirely green. Two faults in
# one: a browser launched on the clock whether or not anything needed a token,
# and an absent token read as a failure to mint one when it in fact meant
# there had been no challenge to pass. Both are decisions `mint` makes before
# and after the browser, which is the part of it that can be tested.


class FakeConn:

    """Enough of a connection for `Run` to open a run and a stage on it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, params: object = None) -> FakeConn:
        self.statements.append(" ".join(sql.split()))
        return self

    def fetchone(self) -> dict[str, int]:
        return {"id": 1}


def _run(trigger: str = "schedule") -> Run:
    return Run(FakeConn(), job="clearance", trigger=trigger)  # type: ignore[arg-type]


def test_no_browser_is_launched_when_the_reader_is_not_being_challenged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The point of the gate. Chromium on a 4 GB box is the most expensive thing
    # this project does, and a token nothing is asking for is worth none of it.
    monkeypatch.setattr(clearance.store, "challenged_recently", lambda *a, **k: 0)

    def refuse(_: str) -> tuple[list[dict[str, object]], int]:
        raise AssertionError("the browser must not be launched without a challenge")

    monkeypatch.setattr(clearance, "_open_and_collect", refuse)

    # "ok", not "degraded" and not a skip: being served the page is the outcome
    # this whole module exists to restore, so reaching it is not a bad run.
    assert clearance.mint(FakeConn(), _run()) == "ok"  # type: ignore[arg-type]


def test_asking_for_a_mint_by_hand_runs_the_browser_whatever_the_refusals_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate is on the timer, not on the job. Checking the installed Chromium
    # against TARGET is the answer to a mint rate that sits low, and a gate that
    # also refused a person at a terminal would make it impossible on exactly
    # the days there is nothing else wrong.
    monkeypatch.setattr(clearance.store, "challenged_recently", lambda *a, **k: 0)
    opened: list[str] = []

    def collect(proxy: str) -> tuple[list[dict[str, object]], int]:
        opened.append(proxy)
        return [_cookie()], 403

    monkeypatch.setattr(clearance, "_open_and_collect", collect)
    monkeypatch.setattr(clearance, "_verify", lambda *a, **k: 200)

    assert clearance.mint(FakeConn(), _run("manual")) == "ok"  # type: ignore[arg-type]
    assert opened == [""]


def test_a_page_served_without_a_challenge_is_not_a_degraded_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reader was challenged inside the window, so the gate let this through
    # — and by the time the browser asked, Cloudflare had relented. There is no
    # token on an unchallenged page and nothing to fix about its absence.
    monkeypatch.setattr(clearance.store, "challenged_recently", lambda *a, **k: 1)
    monkeypatch.setattr(clearance, "_open_and_collect", lambda _: ([], 200))

    assert clearance.mint(FakeConn(), _run()) == "ok"  # type: ignore[arg-type]


def test_a_challenge_the_browser_did_not_clear_is_still_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The case the module is for, and the one that must keep reporting: a
    # challenge was served, the browser was given its sixty seconds, and it
    # came back without a token. That is a reader left under a 403.
    monkeypatch.setattr(clearance.store, "challenged_recently", lambda *a, **k: 1)
    monkeypatch.setattr(
        clearance, "_open_and_collect", lambda _: ([_cookie(name="__cf_bm")], 403)
    )

    assert clearance.mint(FakeConn(), _run()) == "degraded"  # type: ignore[arg-type]
