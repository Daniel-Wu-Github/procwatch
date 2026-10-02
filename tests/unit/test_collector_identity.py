"""Process identity regressions; all process reads are simulated, never signalled."""

from contextlib import nullcontext
from types import SimpleNamespace

import psutil
import pytest

from procwatch.collector import collect


class ProcessTable:
    """Model psutil's cached Process identity and PID-based field reads."""

    def __init__(self):
        self.pid = 424242
        self.started = 100.0
        self.failure_field = None
        self.failure_type = None
        self.cached = None

    def process(self, pid):
        assert pid == self.pid
        return FakeProcess(self)

    def process_iter(self, attrs=None, ad_value=None):
        if self.cached is None:
            self.cached = self.process(self.pid)
        if attrs:
            self.cached.info = self.cached.as_dict(attrs, ad_value)
        return iter([self.cached])

    def clear_cache(self):
        self.cached = None


class FakeProcess:
    def __init__(self, table):
        self.table = table
        self.pid = table.pid
        # psutil caches create_time on a Process object even when its PID is reused.
        self.started = table.started

    def create_time(self):
        return self.started

    def oneshot(self):
        return nullcontext()

    def is_running(self):
        return self.started == self.table.started

    def as_dict(self, attrs=None, ad_value=None):
        result = {}
        for name in attrs or ():
            try:
                result[name] = self.pid if name == "pid" else getattr(self, name)()
            except psutil.AccessDenied:
                result[name] = ad_value
        return result

    def read(self, field, value):
        if field == self.table.failure_field:
            raise self.table.failure_type(self.pid)
        return value

    def ppid(self):
        return self.read("ppid", 10)

    def name(self):
        return self.read("name", "worker")

    def exe(self):
        return self.read("exe", "/opt/bin/worker")

    def cwd(self):
        return self.read("cwd", "/projects/worker")

    def cmdline(self):
        return self.read("cmdline", ["worker", "--serve"])

    def username(self):
        return self.read("username", "alice")

    def memory_info(self):
        return self.read("memory_info", SimpleNamespace(rss=4096))

    def cpu_percent(self, interval=None):
        return self.read("cpu_percent", 12.5)


@pytest.fixture
def process_table(monkeypatch):
    table = ProcessTable()

    def process_iter(attrs=None, ad_value=None):
        return table.process_iter(attrs, ad_value)

    process_iter.cache_clear = table.clear_cache
    monkeypatch.setattr(psutil, "process_iter", process_iter)
    monkeypatch.setattr(psutil, "Process", table.process)
    monkeypatch.setattr(psutil, "pids", lambda: [table.pid])
    return table


def test_each_collection_uses_current_identity_after_cached_pid_is_reused(process_table):
    first = collect()
    assert [(p.pid, p.create_time) for p in first] == [(process_table.pid, 100.0)]

    process_table.started = 200.0
    second = collect()
    assert [(p.pid, p.create_time) for p in second] == [(process_table.pid, 200.0)]


@pytest.mark.parametrize("field", ["ppid", "name", "exe", "cwd", "cmdline", "username", "memory_info", "cpu_percent"])
@pytest.mark.parametrize("failure_type", [psutil.NoSuchProcess, psutil.ZombieProcess])
def test_process_gone_during_any_field_read_is_omitted(process_table, field, failure_type):
    process_table.failure_field = field
    process_table.failure_type = failure_type
    assert collect() == []


@pytest.mark.parametrize(
    ("field", "attribute", "default"),
    [("exe", "exe", None), ("cwd", "cwd", None), ("cmdline", "cmdline", ()),
     ("username", "username", None), ("memory_info", "rss", 0), ("cpu_percent", "cpu", 0.0)],
)
def test_access_denied_field_keeps_process_with_default(process_table, field, attribute, default):
    process_table.failure_field = field
    process_table.failure_type = psutil.AccessDenied
    procs = collect()
    assert len(procs) == 1
    assert procs[0].pid == process_table.pid
    assert procs[0].create_time == 100.0
    assert getattr(procs[0], attribute) == default


def test_pid_reused_while_fields_are_read_is_omitted(process_table, monkeypatch):
    original_read = FakeProcess.read

    def read_and_replace(self, field, value):
        result = original_read(self, field, value)
        if field == "memory_info":
            process_table.started = 200.0
        return result

    monkeypatch.setattr(FakeProcess, "read", read_and_replace)
    assert collect() == []
