"""Which source a portal job is recorded and gated against.

Two jobs can read one site: `zoopla` sweeps the subscribed districts and
`zoopla_london` sweeps the whole city, both writing `listings.source_key =
'zoopla'`. So a job name is not a source key, and every question `run_job`
asks the database about a source has to be asked with the reader's own key.

Worth its own file because both ways of getting this wrong fail silently.
`sources.announces` answers False for a key it has never heard of — fail
closed, which is right — so a gate keyed by job name does not raise, it just
tells nobody; and a reader passed unbuilt dies inside the sweep, where the
handler reports it as one portal having failed.
"""

from __future__ import annotations

from typing import Any

from worker import store
from worker.config import Config
from worker.pipeline import run as runner
from worker.sources import sweep

CFG = Config(database_url="", telegram_token=None, run_url=None, dry_run=False)


class Job:
    """Just enough of `obs.log.Run` for the portal branch to report against."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def event(self, level: str, message: str, **_: object) -> None:
        self.said.append(f"{level}: {message}")


def ran(
    monkeypatch: Any,
    *,
    job: str,
    source_key: str | None = None,
    live: set[str] | None = None,
) -> tuple[list[Any], list[str], str]:
    """Run one portal job with the sweep and the queue stubbed out.

    Returns the readers the sweep was handed, the source keys the queue was
    keyed to, and the job's status.
    """

    readers: list[Any] = []
    keyed: list[str] = []

    def collect(conn: object, run: object, portal: Any, **_: object) -> sweep.Sweep:
        readers.append(portal)
        return sweep.Sweep(stored=[1], announce=[1])

    monkeypatch.setattr(sweep, "collect", collect)
    monkeypatch.setattr(
        runner,
        "queue_matches",
        lambda conn, run, *, source_key, listing_ids: keyed.append(source_key),
    )
    monkeypatch.setattr(
        store,
        "enabled_sources",
        lambda conn: {"rightmove", "zoopla", "openrent", "spareroom"}
        if live is None
        else live,
    )

    job_run = Job()
    status = runner.run_job(
        None,  # type: ignore[arg-type]
        job_run,  # type: ignore[arg-type]
        job=job,
        source_key=source_key,
        cfg=CFG,
    )
    return readers, keyed, status


def test_the_whole_city_sweep_is_queued_under_zoopla(monkeypatch: Any) -> None:
    # The bug this closes, measured against the live table: `sources` holds no
    # `zoopla_london` row — 0061 records that it should not — so keyed by job
    # name every listing this sweep found was "queued for nobody", every five
    # minutes of the working day, while the stage said `muted` and the run
    # said ok.
    _, keyed, status = ran(monkeypatch, job="zoopla_london")

    assert keyed == ["zoopla"]
    assert status == "ok"


def test_a_job_named_after_its_source_is_unaffected(monkeypatch: Any) -> None:
    # Three of the five jobs are named after the key they write, and the fix
    # must not move them.
    _, keyed, _ = ran(monkeypatch, job="rightmove")

    assert keyed == ["rightmove"]


def test_a_manual_sweep_of_everything_keys_each_portal_to_its_own_source(
    monkeypatch: Any,
) -> None:
    _, keyed, _ = ran(monkeypatch, job="portals")

    # Five jobs, four sources: both Zoopla readers answer `zoopla`.
    assert sorted(keyed) == ["openrent", "rightmove", "spareroom", "zoopla", "zoopla"]


def test_an_explicit_source_hands_the_sweep_a_built_reader(monkeypatch: Any) -> None:
    # `PORTAL_JOBS` holds thunks so that importing this module needs no
    # scraping extra on the delivery host. The explicit-source path used to
    # pass one of them straight through, so `--source zoopla_london` sent a
    # function where a reader belongs.
    readers, keyed, status = ran(
        monkeypatch, job="portals", source_key="zoopla_london"
    )

    assert not callable(readers[0])
    # Built *and* configured: the region is what distinguishes this reader
    # from the district one, and both come from the same class.
    assert readers[0].regions == ("london",)
    assert keyed == ["zoopla"]
    assert status == "ok"


def test_a_source_that_is_not_a_portal_is_an_error_not_a_sweep(
    monkeypatch: Any,
) -> None:
    readers, keyed, status = ran(monkeypatch, job="portals", source_key="tg_feed")

    assert status == "failed"
    assert (readers, keyed) == ([], [])


def test_a_disabled_site_stops_both_of_its_jobs(monkeypatch: Any) -> None:
    # Asked of the reader's key, which is the half that already worked: one
    # `UPDATE sources SET enabled = false WHERE key = 'zoopla'` has to stop
    # the district reader and the city sweep together.
    readers, keyed, status = ran(monkeypatch, job="portals", live={"rightmove"})

    assert [one.key for one in readers] == ["rightmove"]
    assert keyed == ["rightmove"]
    assert status == "ok"
