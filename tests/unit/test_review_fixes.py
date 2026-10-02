"""Fixes from the independent core review: error isolation, protected prefixes, startup ancestors, shutdown wait."""
import os
import signal
from types import SimpleNamespace

import pytest

from procwatch import cli
from procwatch.killer import StopPlan, Target, execute
from procwatch.safety import check

KW = dict(me='tester', self_pid=500, ancestors_of_self=frozenset())


def test_one_unexpected_failure_never_aborts_the_rest_of_the_batch():
    plan = StopPlan((Target(10, 1.0), Target(11, 1.0), Target(12, 1.0)), ())
    sent = []
    def create_time_of(pid):
        if pid == 11:
            raise RuntimeError('collector hiccup')
        return 1.0
    outcomes = execute(plan, signal.SIGTERM, signal_fn=lambda pid, sig: sent.append(pid), create_time_of=create_time_of)
    assert [(o.pid, o.status) for o in outcomes] == [(10, 'signalled'), (11, 'error'), (12, 'signalled')]
    assert sent == [10, 12]


def test_a_failing_signal_function_is_recorded_not_raised():
    def boom(pid, sig):
        raise RuntimeError('unexpected')
    outcomes = execute(StopPlan((Target(10, 1.0),), ()), signal.SIGTERM, signal_fn=boom, create_time_of=lambda pid: 1.0)
    assert outcomes[0].status == 'error'


@pytest.mark.parametrize('exe', ['/usr/sbin/cfprefsd', '/sbin/launchd', '/usr/libexec/distnoted',
                                 '/System/Cryptexes/App/usr/libexec/safarid', '/System/Cryptexes/OS/usr/sbin/thing',
                                 '/System/Volumes/Preboot/Cryptexes/OS/usr/libexec/thing',
                                 '/Library/Apple/System/Library/CoreServices/XProtect.app/Contents/MacOS/XProtect'])
def test_user_owned_apple_daemons_are_protected_by_path(proc, exe):
    verdict = check(proc(10, name='daemon', exe=exe), **KW)
    assert not verdict.allowed and 'macOS' in verdict.reason


@pytest.mark.parametrize('exe', ['/System/Cryptexes/App/System/Applications/Safari.app/Contents/MacOS/Safari',
                                 '/System/Applications/Music.app/Contents/MacOS/Music', '/usr/local/bin/node',
                                 '/Applications/Library/Apple/x', '/usr/sbin-fake/x', '/opt/homebrew/bin/python3'])
def test_apps_and_third_party_binaries_stay_stoppable(proc, exe):
    assert check(proc(10, name='thing', exe=exe), **KW).allowed


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path / 'lease'))
    seen, waits = {}, []
    def interrupt(): raise KeyboardInterrupt
    def fake(**kw):
        seen.update(kw)
        return SimpleNamespace(server_address=('127.0.0.1', 1234), serve_forever=interrupt, server_close=lambda: None,
                               wait_for_actions=lambda timeout: waits.append(timeout) or True)
    monkeypatch.setattr(cli, 'create_server', fake)
    return (lambda: cli.main(['--no-browser', '--rules', str(tmp_path / 'no-rules')])), seen, waits


def test_cli_passes_the_terminal_chain_captured_at_startup(run):
    go, seen, _ = run
    assert go() == 0
    assert isinstance(seen['startup_ancestors'], frozenset) and os.getppid() in seen['startup_ancestors']
    assert os.getpid() not in seen['startup_ancestors']


def test_cli_waits_a_bounded_time_for_an_in_flight_stop_before_exiting(run):
    go, _, waits = run
    assert go() == 0 and len(waits) == 1 and 0 < waits[0] <= 15
