"""Stop/Force signal exactly what the user confirmed, and a stale click can never hit a reused pid.
Fake processes and a recording signal function over real loopback sockets: nothing real is ever signalled."""
import json
import signal
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = 'tok-1234'
ME = 'tester'
MACH = MachineStats(1000, 500, 10.0, 2, None)


def P(pid, ppid=1, name='app', create_time=1.0, username=ME):
    return Proc(pid, ppid, name, '/Applications/App.app/Contents/MacOS/app', None, (name,), username, 100, 1.0, create_time)


class World:
    def __init__(self, procs):
        self.procs, self.signals, self.collects = list(procs), [], 0
        self.delay = 0.0

    def collect(self):
        self.collects += 1
        return list(self.procs)

    def signal(self, pid, sig):
        time.sleep(self.delay)
        self.signals.append((pid, sig))


@pytest.fixture
def serve():
    started = []
    def build(world, **kw):
        kw.setdefault('allow_kill', True)
        server = create_server(collect_fn=world.collect, machine_fn=lambda: MACH, signal_fn=world.signal, token=TOKEN,
                               projects_root='/projects', me=ME, self_pid=500, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server
    yield build
    for server in started:
        try:
            server.shutdown()
        except Exception:
            pass
        server.server_close()


def post(server, path, body, token=TOKEN):
    port = server.server_address[1]
    request = Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': token, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def tree():
    return World([P(10, name='parent', create_time=1.5), P(11, ppid=10, name='child', create_time=2.5)])


def test_preview_lists_targets_with_their_start_times_children_first(serve):
    server = serve(tree())
    status, data = post(server, '/api/preview', {'pids': [10]})
    assert status == 200 and data['pids'] == [11, 10]
    assert data['targets'] == [{'pid': 11, 'create_time': 2.5}, {'pid': 10, 'create_time': 1.5}]


def test_confirmed_targets_are_signalled_exactly_in_the_confirmed_order(serve):
    world = tree()
    server = serve(world)
    targets = post(server, '/api/preview', {'pids': [10]})[1]['targets']
    status, data = post(server, '/api/stop', {'confirmed': targets})
    assert status == 200 and [o['status'] for o in data['outcomes']] == ['signalled', 'signalled']
    assert world.signals == [(11, signal.SIGTERM), (10, signal.SIGTERM)]
    post(server, '/api/force', {'confirmed': targets})
    assert world.signals[-2:] == [(11, signal.SIGKILL), (10, signal.SIGKILL)]


def test_a_pid_reused_after_the_preview_is_never_signalled(serve):
    world = tree()
    server = serve(world)
    targets = post(server, '/api/preview', {'pids': [10]})[1]['targets']
    world.procs[0] = P(10, name='someone-else', create_time=99.0)      # same pid, a different process
    status, data = post(server, '/api/stop', {'confirmed': targets})
    outcomes = {o['pid']: o['status'] for o in data['outcomes']}
    assert status == 200 and outcomes == {11: 'signalled', 10: 'reused'}
    assert world.signals == [(11, signal.SIGTERM)]


def test_a_process_that_exited_after_the_preview_is_reported_gone(serve):
    world = tree()
    server = serve(world)
    targets = post(server, '/api/preview', {'pids': [10]})[1]['targets']
    world.procs.pop()                                                  # the child is gone
    data = post(server, '/api/stop', {'confirmed': targets})[1]
    assert {o['pid']: o['status'] for o in data['outcomes']} == {11: 'gone', 10: 'signalled'}


def test_a_child_spawned_after_the_preview_is_not_signalled(serve):
    world = tree()
    server = serve(world)
    targets = post(server, '/api/preview', {'pids': [10]})[1]['targets']
    world.procs.append(P(12, ppid=10, name='newcomer', create_time=3.0))
    post(server, '/api/stop', {'confirmed': targets})
    assert [pid for pid, _ in world.signals] == [11, 10]


def test_protected_processes_in_a_confirmed_list_are_still_refused(serve):
    world = World([P(10), P(500, name='procwatch'), P(900, name='root-daemon', username='root')])
    server = serve(world)
    forged = [{'pid': 1, 'create_time': 1.0}, {'pid': 500, 'create_time': 1.0}, {'pid': 900, 'create_time': 1.0}]
    status, data = post(server, '/api/force', {'confirmed': forged})
    assert status == 200 and world.signals == [] and data['outcomes'] == []
    assert {r['pid'] for r in data['refused']} == {1, 500, 900}


@pytest.mark.parametrize('body', [
    {}, {'confirmed': []}, {'confirmed': 'x'}, {'confirmed': [{'pid': 10}]}, {'confirmed': [{'create_time': 1.0}]},
    {'confirmed': [{'pid': '10', 'create_time': 1.0}]}, {'confirmed': [{'pid': True, 'create_time': 1.0}]},
    {'confirmed': [{'pid': -5, 'create_time': 1.0}]}, {'confirmed': [{'pid': 10, 'create_time': 'x'}]},
    {'confirmed': [{'pid': 10, 'create_time': 1.0, 'extra': 1}]}, {'confirmed': [10]},
    {'confirmed': [{'pid': 10, 'create_time': 1.5}], 'pids': [10]}, {'confirmed': [{'pid': 10, 'create_time': 1.5}], 'groups': ['x']},
    {'confirmed': [{'pid': i, 'create_time': 1.0} for i in range(3000)]},
])
def test_malformed_confirmed_lists_are_rejected_and_signal_nothing(serve, body):
    world = tree()
    server = serve(world)
    assert post(server, '/api/stop', body)[0] == 400 and world.signals == []


def test_duplicates_in_a_confirmed_list_are_signalled_once(serve):
    world = tree()
    server = serve(world)
    entry = {'pid': 11, 'create_time': 2.5}
    post(server, '/api/stop', {'confirmed': [entry, entry]})
    assert world.signals == [(11, signal.SIGTERM)]


def test_read_only_mode_still_refuses_confirmed_actions(serve):
    world = tree()
    server = serve(world, allow_kill=False)
    assert post(server, '/api/stop', {'confirmed': [{'pid': 11, 'create_time': 2.5}]})[0] == 403 and world.signals == []


def test_a_large_confirmed_stop_reads_the_process_table_once(serve):
    world = World([P(100 + i, create_time=float(i)) for i in range(40)])
    server = serve(world)
    targets = [{'pid': 100 + i, 'create_time': float(i)} for i in range(40)]
    before = world.collects
    status, data = post(server, '/api/stop', {'confirmed': targets})
    assert status == 200 and len(world.signals) == 40 and world.collects - before == 1


@pytest.mark.parametrize('path', ['/api/stop', '/api/force'])
@pytest.mark.parametrize('body', [{'pids': [10]}, {'groups': ['x']}, {'pids': [10], 'groups': ['x']}])
def test_the_old_pids_and_groups_body_can_no_longer_signal_anything(serve, path, body):
    world = tree()
    server = serve(world)
    status, data = post(server, path, body)
    assert status == 400 and 'confirmed' in data['error'] and world.signals == []


def test_startup_ancestors_stay_protected_even_if_the_live_chain_is_broken(serve):
    world = World([P(40, name='Terminal'), P(41, ppid=9999, name='zsh'), P(42, ppid=41, name='procs')])
    server = serve(world, startup_ancestors=frozenset({40}))
    data = post(server, '/api/preview', {'pids': [40]})[1]
    assert data['pids'] == [] and data['refused'][0]['reason'] == 'This process launched procwatch'


def test_shutdown_can_wait_for_an_in_flight_stop_to_finish(serve):
    world = World([P(100 + i, create_time=float(i)) for i in range(4)])
    world.delay = 0.25
    server = serve(world)
    targets = [{'pid': 100 + i, 'create_time': float(i)} for i in range(4)]
    assert server.wait_for_actions(1) is True                          # idle: returns at once
    worker = threading.Thread(target=lambda: post(server, '/api/stop', {'confirmed': targets}))
    worker.start()
    time.sleep(0.3)                                                    # the stop is part-way through
    assert len(world.signals) < 4
    assert server.wait_for_actions(5) is True and len(world.signals) == 4
    worker.join(5)


def test_page_confirms_exact_targets_and_allows_a_long_stop():
    from pathlib import Path
    html = Path('procwatch/page.html').read_text()
    assert '{confirmed:' in html and 'p.targets' in html and '60000' in html
