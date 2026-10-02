from concurrent.futures import ThreadPoolExecutor
import re
import subprocess

import psutil

from .model import MachineStats, Proc


_reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='procwatch-sampler')
_errors = (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess)


def _read(fn, default):
    try:
        return fn()
    except psutil.AccessDenied:
        return default


def _collect():
    result = {}
    for p in psutil.process_iter():
        try:
            # Keep live cached objects for cpu_percent's sampling baseline, but
            # replace objects whose PID now belongs to a different process.
            if not p.is_running():
                p = psutil.Process(p.pid)
            started = _read(p.create_time, 0.0)
            owner = _read(p.username, None)
            # An unreadable identity must never become a signal target.
            if not started:
                owner = None
            memory = _read(p.memory_info, None)
            proc = Proc(
                pid=p.pid, ppid=_read(p.ppid, 0), name=_read(p.name, f'pid {p.pid}'),
                exe=_read(p.exe, None) or None, cwd=_read(p.cwd, None) or None,
                cmdline=tuple(_read(p.cmdline, ()) or ()), username=owner,
                rss=max(0, memory.rss) if memory else 0,
                cpu=max(0.0, _read(lambda: p.cpu_percent(None), 0.0)), create_time=started)
            # Field reads address a PID: discard a row if that PID was reused
            # or exited while its fields were being collected.
            if p.is_running():
                result[p.pid] = proc
        except _errors:
            continue
    return list(result.values())


def collect():
    return _reader.submit(_collect).result()


def _gpu():
    try:
        output = subprocess.run(['ioreg', '-r', '-d', '1', '-w', '0', '-c', 'IOAccelerator'],
                                capture_output=True, text=True, timeout=0.75, check=True).stdout
        values = []
        for stats in re.findall(r'"PerformanceStatistics"\s*=\s*\{([^}]*)\}', output):
            values.extend(float(v) for v in re.findall(r'"Device Utilization %"\s*=\s*(\d+(?:\.\d+)?)', stats))
        return min(100.0, max(0.0, max(values))) if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _machine():
    memory = psutil.virtual_memory()
    return MachineStats(memory.total, min(memory.total, max(0, memory.available)),
                        min(100.0, max(0.0, psutil.cpu_percent(interval=None))),
                        psutil.cpu_count() or 1, _gpu())


def machine():
    return _reader.submit(_machine).result()


# Use one sampling thread: psutil's machine CPU baseline is thread-local.
_reader.submit(lambda: psutil.cpu_percent(interval=None)).result()
collect()
