from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str
    exe: str | None
    cwd: str | None
    cmdline: tuple[str, ...]
    username: str | None
    rss: int
    cpu: float
    create_time: float


@dataclass(frozen=True)
class MachineStats:
    mem_total: int
    mem_available: int
    cpu_busy_pct: float
    cpu_cores: int
    gpu_busy_pct: float | None


def ancestors(pid: int, procs: Sequence[Proc]) -> frozenset[int]:
    table = {p.pid: p for p in procs}
    seen = {pid}
    result = set()
    while pid in table:
        pid = table[pid].ppid
        if pid in seen or pid not in table:
            break
        seen.add(pid)
        result.add(pid)
        if pid <= 1:
            break
    return frozenset(result)
