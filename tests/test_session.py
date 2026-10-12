"""The memory that outlives a run, on its own terms.

`test_fetch.py` covers what the transport does with this; these cover the thing
itself, and they are mostly about the cases where it must refuse to break. A
scraper with no memory still scrapes. A scraper that raises while deciding
which exit address to use does not, and it is the readers' only route to two
of the three portals — so "nothing here may ever raise" is behaviour worth
pinning rather than a remark in a docstring.
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest

from worker.sources import session as mem

PORTS = tuple(range(10000, 10010))


def test_the_chosen_exit_survives_a_new_session_object() -> None:
    # Which is the whole purpose: a run is a process, and two Session objects
    # a few minutes apart are two runs.
    first = mem.Session.load("zoopla")
    chosen = first.pick(PORTS)
    assert first.save() is True

    assert mem.Session.load("zoopla").pick(PORTS) == chosen


def test_two_readers_do_not_see_each_other_s_exit() -> None:
    # The property the pid offset used to provide, and the reason this is keyed
    # by name. Three readers on one address means one burned exit takes all
    # three out, and the day's traffic arrives at the portal from one house.
    one = mem.Session.load("zoopla")
    one.pick(PORTS)
    one.save()

    assert mem.Session.load("openrent").port is None


def test_a_burned_exit_goes_on_cooldown_and_is_not_drawn_again() -> None:
    kept = mem.Session.load("zoopla")
    first = kept.pick(PORTS)
    second = kept.burn(PORTS)

    assert second != first
    assert first in kept.cooldown
    # Not merely absent from the next draw: absent from every draw while there
    # is anything else to draw. The old rotation advanced an index, so over a
    # range of 501 it came back round to a refused address in time.
    assert all(kept.burn(PORTS) != first for _ in range(len(PORTS) - 2))


def test_a_pool_that_is_entirely_on_cooldown_still_answers() -> None:
    # Deliberate, and the alternative is worse. A reader with nothing left to
    # draw should ask again from the least-recently-refused address rather
    # than decline to work — an hour of refusing every exit is an hour of a
    # reader announcing nothing, and the refusals may not have been about us.
    kept = mem.Session.load("zoopla")
    kept.pick(PORTS)
    for _ in range(len(PORTS) * 2):
        assert kept.burn(PORTS) in PORTS


def test_cooldown_expires_so_a_recovered_address_comes_back() -> None:
    kept = mem.Session.load("zoopla")
    gone = kept.pick(PORTS)
    kept.burn(PORTS)
    assert gone in kept.cooldown

    kept.cooldown[gone] = time.time() - 1
    kept.expire()
    assert gone not in kept.cooldown


def test_an_exit_held_past_its_age_is_retired() -> None:
    # Not needed for correctness — a dead sticky session refuses and `burn`
    # handles it — but it bounds how much of a day arrives from one address.
    kept = mem.Session.load("zoopla")
    old = kept.pick(PORTS)
    kept.since = time.time() - mem.MAX_AGE - 1

    assert kept.pick(PORTS) != old


def test_an_exit_from_a_different_proxy_is_not_reused() -> None:
    # A leftover from another SCRAPE_PROXY. Reconnecting on it would aim the
    # run at a port nobody is listening on.
    kept = mem.Session.load("zoopla")
    kept.port = 9999
    kept.since = time.time()

    assert kept.pick(PORTS) in PORTS


def test_a_rotating_proxy_has_no_ports_and_no_opinion_about_them() -> None:
    # Port 823 assigns an address per connection, so `exit_ports` returns just
    # that one and there is nothing here to remember.
    kept = mem.Session.load("zoopla")
    assert kept.pick(()) is None
    assert kept.burn(()) is None


def test_the_cooldown_list_cannot_grow_without_bound() -> None:
    kept = mem.Session.load("zoopla")
    wide = tuple(range(10000, 10000 + mem.REMEMBERED * 3))
    for _ in range(mem.REMEMBERED * 2):
        kept.burn(wide)

    assert len(kept.cooldown) <= mem.REMEMBERED


def test_a_block_is_believed_and_then_expires() -> None:
    kept = mem.Session.load("zoopla")
    kept.remember("www.zoopla.co.uk")
    assert kept.refuses("www.zoopla.co.uk") is True
    # Case is not evidence about anything.
    assert kept.refuses("WWW.Zoopla.co.UK") is True

    kept.blocked["www.zoopla.co.uk"] = time.time() - 1
    kept.expire()
    assert kept.refuses("www.zoopla.co.uk") is False


def test_a_block_is_dropped_the_moment_the_portal_serves_us() -> None:
    kept = mem.Session.load("zoopla")
    kept.remember("www.openrent.co.uk")
    kept.forget("www.openrent.co.uk")

    assert kept.refuses("www.openrent.co.uk") is False


# ── the jars ────────────────────────────────────────────────────────────────


def test_one_jar_per_exit_address_and_per_browser() -> None:
    # Both halves matter and neither is tidiness. `__cf_bm` is issued against
    # the peer that asked for it, so replaying one from another exit is a
    # cookie that disagrees with the connection — worse than no cookie at all.
    # And `cf_clearance` is bound to the user agent that earned it, which
    # `impersonate` moves together with the TLS fingerprint.
    kept = mem.Session.load("zoopla")
    jars = {
        kept.jar("10001", "chrome150"),
        kept.jar("10002", "chrome150"),
        kept.jar("10001", "firefox147"),
    }

    assert len(jars) == 3
    assert None not in jars


def test_a_jar_path_is_safe_to_write_whatever_the_name_was() -> None:
    kept = mem.Session.load("zoopla/../../etc")
    jar = kept.jar("direct", "chrome150")

    assert jar is not None
    assert jar.parent == kept.path.parent / "cookies"  # type: ignore[union-attr]
    assert ".." not in jar.name and "/" not in jar.name


def test_stale_jars_are_deleted_and_live_ones_are_not() -> None:
    # A jar whose `__cf_bm` expired half a day ago is a file, not a session,
    # and there are 501 exit addresses to accumulate them for.
    kept = mem.Session.load("zoopla")
    fresh = kept.jar("10001", "chrome150")
    stale = kept.jar("10002", "chrome150")
    assert fresh is not None and stale is not None
    fresh.write_text("# fresh")
    stale.write_text("# stale")
    import os

    old = time.time() - mem.JAR_FOR - 60
    os.utime(stale, (old, old))

    assert kept.prune_jars() == 1
    assert fresh.exists() and not stale.exists()


# ── refusing to break ───────────────────────────────────────────────────────


def test_a_corrupt_file_is_read_as_an_absent_one() -> None:
    # A run killed mid-write, which `save` is written to avoid but cannot
    # promise about a file somebody edited. Starting again beats raising in
    # the middle of deciding how to reach a portal.
    kept = mem.Session.load("zoopla")
    assert kept.path is not None
    kept.path.write_text("{not json at all")

    again = mem.Session.load("zoopla")
    assert again.port is None
    assert again.pick(PORTS) in PORTS


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"port": "ten thousand"}',
        '{"cooldown": {"not-a-port": 1}, "blocked": {"a": "soon"}}',
        '{"port": 10001, "since": null}',
    ],
)
def test_nonsense_in_the_file_is_survived_field_by_field(payload: str) -> None:
    kept = mem.Session.load("zoopla")
    assert kept.path is not None
    kept.path.write_text(payload)

    again = mem.Session.load("zoopla")
    assert again.pick(PORTS) in PORTS


def test_nowhere_writable_is_a_session_that_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    # A read-only disk is a reason to scrape without memory and not a reason
    # not to scrape. Everything still answers; nothing is written.
    monkeypatch.setattr(mem, "state_dir", lambda: None)
    kept = mem.Session.load("zoopla")

    assert kept.path is None
    assert kept.pick(PORTS) in PORTS
    assert kept.jar("10001", "chrome150") is None
    assert kept.prune_jars() == 0
    assert kept.save() is False


def test_a_directory_that_cannot_be_made_is_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    wanted = tmp_path / "taken"
    # A file where the directory should be: `mkdir` raises, and the answer is
    # "no memory" rather than a traceback out of the transport's constructor.
    wanted.write_text("in the way")
    monkeypatch.setenv("SCRAPE_STATE_DIR", str(wanted))

    assert mem.state_dir() is None


def test_saving_is_atomic_and_leaves_no_half_written_file() -> None:
    kept = mem.Session.load("zoopla")
    kept.pick(PORTS)
    kept.remember("www.zoopla.co.uk")
    assert kept.save() is True
    assert kept.path is not None

    # Readable as a whole, and the temporary file is gone rather than left to
    # be mistaken for the real one by the next run.
    written = json.loads(kept.path.read_text())
    assert written["port"] == kept.port
    assert list(written["blocked"]) == ["www.zoopla.co.uk"]
    assert not list(kept.path.parent.glob("*.tmp"))


def test_nothing_is_written_when_nothing_changed() -> None:
    # A reader that was served on the exit it already had should not rewrite
    # the file twelve times an hour to say so.
    kept = mem.Session.load("zoopla")
    kept.pick(PORTS)
    assert kept.save() is True
    assert kept.save() is False


def test_the_state_directory_prefers_the_explicit_one_over_systemd_s(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    mine = tmp_path / "mine"
    theirs = tmp_path / "theirs"
    monkeypatch.setenv("SCRAPE_STATE_DIR", str(mine))
    monkeypatch.setenv("STATE_DIRECTORY", str(theirs))

    assert mem.state_dir() == mine


def test_systemd_s_list_of_state_directories_takes_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    # systemd passes a colon-separated list when the unit names more than one,
    # and a path with a colon in it is not a path this would ever be given.
    import os

    first = tmp_path / "first"
    monkeypatch.delenv("SCRAPE_STATE_DIR")
    monkeypatch.setenv(
        "STATE_DIRECTORY", os.pathsep.join([str(first), str(tmp_path / "second")])
    )

    assert mem.state_dir() == first
