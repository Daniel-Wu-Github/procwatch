from collections import defaultdict
from dataclasses import dataclass
import signal

from .safety import check


@dataclass(frozen=True)
class Target:
    pid: int
    create_time: float


@dataclass(frozen=True)
class StopPlan:
    order: tuple[Target, ...]
    refused: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Outcome:
    pid: int
    status: str


def plan(selection, procs, *, me, self_pid, ancestors_of_self):
    table = {p.pid: p for p in procs}
    children = defaultdict(list)
    for p in procs:
        children[p.ppid].append(p.pid)
    visited, order, refused = set(), [], []
    for root in selection:
        stack = [(root, False)]
        while stack:
            pid, finish = stack.pop()
            if finish:
                order.append(Target(pid, table[pid].create_time))
                continue
            if pid in visited:
                continue
            visited.add(pid)
            p = table.get(pid)
            if p is None:
                refused.append((pid, "Process is gone"))
                continue
            verdict = check(p, me=me, self_pid=self_pid, ancestors_of_self=ancestors_of_self)
            if not verdict.allowed:
                refused.append((pid, verdict.reason))
                continue
            stack.append((pid, True))
            stack.extend((child, False) for child in reversed(children[pid]))
    return StopPlan(tuple(order), tuple(refused))


def execute(stop_plan, sig, *, signal_fn, create_time_of):
    if sig not in (signal.SIGTERM, signal.SIGKILL):
        raise ValueError("Only SIGTERM and SIGKILL are supported")
    outcomes = []
    for target in stop_plan.order:
        try:
            current = create_time_of(target.pid)
            if current is None:
                status = "gone"
            elif current != target.create_time:
                status = "reused"
            else:
                signal_fn(target.pid, sig)
                status = "signalled"
        except ProcessLookupError:
            status = "gone"
        except PermissionError:
            status = "denied"
        except Exception:      # one failure, expected or not, never aborts the rest of the batch
            status = "error"
        outcomes.append(Outcome(target.pid, status))
    return outcomes
