"""What the machine looked like while a run went.

── why this is in the run log and not a metrics agent ───────────────────────

The box is 2 vCPU and 4 GB, it runs nothing but this worker — Postgres is in
Supabase and the web app is on Vercel — and the question it has to answer is
narrow: did a run have the room it needed, and what did the browser cost. A
Prometheus node exporter would answer that and a hundred others, and would
itself want more memory than the thing being measured.

So the sample goes where every other number about a run already goes: the
stage counters. It costs two small reads out of /proc and one statvfs, it is
keyed to the run that took it, and the admin console reads it with the same
query shape it uses for everything else. The cost is resolution — one sample
per run rather than one every fifteen seconds — which is the right trade for
"was the box short of memory at 14:06", and the wrong one for a flame graph.

── nothing here may raise ───────────────────────────────────────────────────

Same rule as `worker.sources.session`. A worker that cannot read /proc still
scrapes; a worker that dies while measuring itself does not. Every reader
below returns None rather than raising, and None means "not measured" all the
way up — the console prints "—" for it rather than a zero, because a zero here
would read as "no memory free".

This is also what makes the module importable on the development Macs, where
/proc does not exist at all.
"""

from __future__ import annotations

import os
import resource
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Where the kernel publishes it on Linux. A constant so the tests can point
#: the readers at a fixture directory instead of mocking `open`.
PROC = Path("/proc")

#: `ru_maxrss` is in kilobytes on Linux and in bytes on macOS. Only the Linux
#: figure is ever reported — the development Macs do not run the browser — but
#: multiplying by 1024 on both would make a local run print gigabytes and
#: somebody would believe it.
_MAXRSS_UNIT = 1 if os.uname().sysname == "Darwin" else 1024


@dataclass(frozen=True)
class Snapshot:
    """The box at one instant. Every field optional; see the module note."""

    #: Processors the kernel will schedule on. The divisor for `load1`.
    cpus: int | None = None
    #: One-minute load average. Not a percentage: a load of 2.0 on 2 vCPU is
    #: "fully busy", and the console divides to get the percentage it prints.
    #: Read rather than sampled because a percentage needs two readings a
    #: moment apart, and a oneshot that sleeps to measure itself is a oneshot
    #: that holds its timer open to no purpose.
    load1: float | None = None
    #: Bytes. `MemAvailable`, not `MemFree`: the kernel's own estimate of what
    #: a new process could get without swapping, which on this box reads ~3.1
    #: GB against ~400 MB free because the rest is reclaimable page cache.
    #: Printing MemFree here would have the console permanently alarmed.
    mem_total: int | None = None
    mem_available: int | None = None
    #: Bytes on the filesystem holding the state directory, which is the one
    #: that fills: the browser's profile and the cookie jars live there.
    disk_total: int | None = None
    disk_free: int | None = None

    def counters(self) -> dict[str, int | float]:
        """The sample as stage counters, omitting whatever was not measured.

        Omitted rather than null, for the reason the module note gives: a
        counter that is absent reads the same in a run from before this module
        existed as in a run on a box with no /proc, and both are honestly "not
        measured".
        """

        named = {
            "host_cpus": self.cpus,
            "host_load1": self.load1,
            "host_mem_total": self.mem_total,
            "host_mem_available": self.mem_available,
            "host_disk_total": self.disk_total,
            "host_disk_free": self.disk_free,
        }
        return {name: value for name, value in named.items() if value is not None}


def snapshot(state: Path | None = None) -> Snapshot:
    """Read the box. Never raises. See the module note."""

    return Snapshot(
        cpus=_cpus(),
        load1=_load1(),
        **_memory(),
        **_disk(state),
    )


def peak_child_bytes() -> int | None:
    """Peak RSS of the largest child process this run has reaped, in bytes.

    Which is how the browser is measured, and the reason no psutil is needed:
    Playwright's Chromium is a child of this process, so once it is closed its
    high-water mark is already recorded in `RUSAGE_CHILDREN` and can simply be
    read back.

    The figure is a **maximum, not a sum** — `ru_maxrss` for children is the
    largest single child, not all of them added up. On the clearance job that
    is the honest number anyway, because the browser is the only child and it
    is the only thing whose size was ever in question. On a job with several
    children it would understate them, which is why nothing else calls this.
    """

    try:
        used = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (OSError, ValueError):
        return None
    return int(used) * _MAXRSS_UNIT if used > 0 else None


def _cpus() -> int | None:
    # `sched_getaffinity` rather than `cpu_count`, because a unit with
    # `CPUAffinity=` or a container with a cpuset is scheduled on fewer
    # processors than the machine has, and the load average has to be read
    # against the number it can actually use.
    #
    # Reached through `getattr` because the call is Linux-only and the
    # development Macs typecheck this file too: on darwin the attribute does
    # not exist at all, so naming it directly is an error there even though the
    # runtime guard below would have caught it.
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            return len(affinity(0))
        except OSError:
            pass
    return os.cpu_count()


def _load1() -> float | None:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def _memory() -> dict[str, int | None]:
    """`MemTotal` and `MemAvailable` from /proc/meminfo, in bytes."""

    wanted = {"MemTotal": "mem_total", "MemAvailable": "mem_available"}
    found: dict[str, int | None] = {"mem_total": None, "mem_available": None}
    try:
        text = (PROC / "meminfo").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return found
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if name not in wanted:
            continue
        # "MemTotal:  3932160 kB" — the unit is always kB on every kernel that
        # has ever published this file, but it is read rather than assumed
        # because a wrong factor of 1024 would be invisible in a tile.
        parts = rest.split()
        if not parts or not parts[0].isdigit():
            continue
        scale = 1024 if len(parts) > 1 and parts[1].lower() == "kb" else 1
        found[wanted[name]] = int(parts[0]) * scale
    return found


def _disk(state: Path | None) -> dict[str, int | None]:
    """Space where the state directory lives, falling back to the root."""

    where = state or Path("/")
    try:
        usage = shutil.disk_usage(where)
    except (OSError, ValueError):
        return {"disk_total": None, "disk_free": None}
    return {"disk_total": int(usage.total), "disk_free": int(usage.free)}


__all__ = ["PROC", "Snapshot", "peak_child_bytes", "snapshot"]
