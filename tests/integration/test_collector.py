"""Reads the real process table (needs psutil)."""

import getpass
import os

import pytest

pytest.importorskip("psutil")

from procwatch.collector import collect, machine  # noqa: E402
from procwatch.model import MachineStats, Proc  # noqa: E402


def test_collect_returns_procs_including_this_very_process():
    procs = collect()
    assert procs and all(isinstance(p, Proc) for p in procs)
    me = next(p for p in procs if p.pid == os.getpid())
    assert me.username == getpass.getuser()
    assert me.rss > 0 and me.create_time > 0
    assert me.cmdline and me.exe


def test_collect_tolerates_unreadable_processes_with_none_fields_not_exceptions():
    procs = collect()                                  # includes root-owned processes we cannot inspect
    assert any(p.pid == 1 for p in procs) or len(procs) > 10
    for p in procs:
        assert isinstance(p.pid, int) and isinstance(p.ppid, int)
        assert p.exe is None or isinstance(p.exe, str)
        assert p.cwd is None or isinstance(p.cwd, str)
        assert isinstance(p.cmdline, tuple)
        assert isinstance(p.rss, int) and p.rss >= 0


def test_collect_has_unique_pids():
    pids = [p.pid for p in collect()]
    assert len(pids) == len(set(pids))


def test_machine_reports_sane_memory_cpu_and_gpu_without_sudo():
    m = machine()
    assert isinstance(m, MachineStats)
    assert m.mem_total > 0 and 0 <= m.mem_available <= m.mem_total
    assert 0.0 <= m.cpu_busy_pct <= 100.0 and m.cpu_cores >= 1
    assert m.gpu_busy_pct is None or 0.0 <= m.gpu_busy_pct <= 100.0
