"""Which degradations are the next run's problem, and which are a person's.

The dashboard's Problems panel acts on this distinction rather than on the
message text, so the flag is the contract between the worker and the panel —
see RECOVERABLE_STREAK in apps/web/lib/admin-queries.ts. A portal refusing
every exit address for one page leaves the watermark where it was, so the next
run reads back over the gap; a channel with no implementation behind it stays
broken until somebody does something.
"""

from __future__ import annotations

from typing import Any

from worker.obs.log import Stage


class Recorder:
    """Just enough of `obs.log.Run` for a stage to report against."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.counters: dict[str, int] = {}

    def event(self, level: str, message: str, **ctx: Any) -> None:
        # The stage passes `stage` and `source_key` through the same call; only
        # the rest is what a panel reads as context.
        rest = {k: v for k, v in ctx.items() if k not in ("stage", "source_key")}
        self.events.append((level, message, rest))

    def count(self, name: str, delta: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + delta


def staged() -> tuple[Stage, Recorder]:
    run = Recorder()
    return Stage(run, 1, "scrape", "zoopla_london"), run  # type: ignore[arg-type]


def test_a_refusal_degrades_the_stage_and_says_the_next_run_fixes_it() -> None:
    stage, run = staged()
    stage.degrade("zoopla read nothing — the one district it asked for was refused",
                  recoverable=True)

    assert stage.status == "degraded"
    assert stage.recoverable is True
    assert run.events[0][0] == "warn"
    assert run.events[0][2] == {"recoverable": True}


def test_a_fault_nobody_but_a_person_can_clear_carries_no_flag() -> None:
    # Absent rather than false, so a row written before the flag existed reads
    # the same as one written now: the panel gets no answer from either, which
    # is the honest answer for a run that predates the question.
    stage, run = staged()
    stage.degrade("channel whatsapp has no implementation")

    assert stage.status == "degraded"
    assert stage.recoverable is False
    assert run.events[0][2] == {}


def test_one_lasting_fault_outweighs_any_number_of_recoverable_ones() -> None:
    # A stage that lost a district to a refusal and also could not read one
    # back to its watermark has one fault that the next run fixes and one it
    # does not. Hiding the pair until a streak builds up would hide the second.
    stage, _ = staged()
    stage.degrade("refused", recoverable=True)
    stage.degrade("did not read back to the watermark")

    assert stage.recoverable is False


def test_the_order_the_two_arrive_in_does_not_change_the_answer() -> None:
    stage, _ = staged()
    stage.degrade("did not read back to the watermark")
    stage.degrade("refused", recoverable=True)

    assert stage.recoverable is False
