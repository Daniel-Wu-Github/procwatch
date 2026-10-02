"""Shared fixtures. `proc` builds a Proc with sensible defaults so each test
only states what it cares about."""

import pytest

from procwatch.model import MachineStats, Proc


@pytest.fixture
def proc():
    def make(pid, ppid=1, name="thing", exe=None, cwd=None, cmdline=(), username="tester",
             rss=0, cpu=0.0, create_time=1.0):
        return Proc(pid=pid, ppid=ppid, name=name, exe=exe, cwd=cwd, cmdline=tuple(cmdline),
                    username=username, rss=rss, cpu=cpu, create_time=create_time)
    return make


@pytest.fixture
def machine():
    def make(mem_total=1000 * 1024 * 1024, mem_available=600 * 1024 * 1024, cpu_busy_pct=40.0, cpu_cores=8, gpu_busy_pct=None):
        return MachineStats(mem_total=mem_total, mem_available=mem_available, cpu_busy_pct=cpu_busy_pct,
                            cpu_cores=cpu_cores, gpu_busy_pct=gpu_busy_pct)
    return make


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Tests never read the developer's real ~/.config or ~/Library."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
