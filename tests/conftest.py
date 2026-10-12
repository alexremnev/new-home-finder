"""Fixtures every test in this directory gets whether it asks or not.

Only one, and it is about a hazard rather than a convenience.

`worker.sources.session` keeps a scraper's cross-run memory in a file: which
exit address was last served, which hosts refuse this machine, and a cookie
jar per exit. Off the server that file lives in the user's cache directory —
which means that without this fixture, running the suite would

  * read whatever a *real* run had left there, so a developer whose last
    `zoopla_london` run recorded a block would see tests fail that pass in CI,
    for reasons nothing in the test says;
  * write to it, so running the tests would plant a fabricated exit port and
    fabricated blocks into the memory the next real run inherits;
  * leak between tests, because the second test to build a `Fetcher` would
    find what the first one persisted.

All three are the same mistake — a test touching state it does not own — and
all three are closed by giving each test its own empty directory. `tmp_path`
is per test, so this is isolation and not merely redirection.

It is autouse because the hazard is in the import, not in the asking: any test
that constructs a `Fetcher`, directly or through `sweep.collect`, gets a
`Session` whether it knows it or not. A fixture that had to be requested would
be a fixture somebody forgets, and the failure mode is a test that passes
while corrupting something.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def isolated_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pathlib.Path]:
    """Point the scrapers' cross-run memory at an empty per-test directory."""

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SCRAPE_STATE_DIR", str(state))
    # `SCRAPE_STATE_DIR` is read first, so these cannot win — unset anyway, so
    # that a test which clears it is testing the fallback rather than silently
    # reaching the real one.
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    yield state
