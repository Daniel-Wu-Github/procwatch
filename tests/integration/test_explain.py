"""Explanation endpoint over real loopback sockets; no inference or signals."""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from procwatch.model import Proc, MachineStats
from procwatch.server import create_server
from procwatch.llm import ExplanationUnavailable


@pytest.fixture
def endpoint():
    seen, signals = [], []
    def explain(facts):
        seen.extend(facts)
        return '<script>example</script> A worker.'
    procs = [Proc(i, 1, 'Python', '/usr/bin/python', '/tmp/work', ('python', '--secret', 'DO_NOT_SEND'), 'me', 123, 0.0, 1.0) for i in range(10, 23)]
    server = create_server(collect_fn=lambda: procs, machine_fn=lambda: MachineStats(1000, 500, 0, 2, None),
                          signal_fn=lambda *args: signals.append(args), token='test-token', projects_root='/tmp',
                          me='me', self_pid=500, allow_kill=False, explain_fn=explain)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen, signals
    server.shutdown(); server.server_close()


def call(port, body, token='test-token'):
    request = Request(f'http://127.0.0.1:{port}/api/explain', data=json.dumps(body).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': token, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response: return response.status, json.loads(response.read())
    except HTTPError as exc: return exc.code, json.loads(exc.read())


def test_explain_works_in_read_only_and_never_signals(endpoint):
    port, seen, signals = endpoint
    status, data = call(port, {'pids': [10]})
    assert status == 200 and data['pids'] == [10] and data['advisory']
    assert data['explanation'].startswith('<script>')
    assert seen[0]['pid'] == 10 and 'DO_NOT_SEND' not in json.dumps(seen)
    assert signals == []


def test_explain_auth_selection_and_bounds(endpoint):
    port, seen, signals = endpoint
    assert call(port, {'pids': [10]}, token='wrong')[0] == 403
    assert call(port, {'pids': list(range(10, 23))})[0] == 400
    assert call(port, {'pids': [999]})[0] == 400
    assert call(port, {'groups': ['missing']})[0] == 400
    assert not seen and not signals


def test_unavailable_upstream_is_503_without_signals():
    signals = []
    p = Proc(10, 1, 'Python', None, None, (), 'me', 0, 0, 1)
    def fail(facts): raise ExplanationUnavailable('Model offline; retry.')
    server = create_server(collect_fn=lambda: [p], machine_fn=lambda: None,
                          signal_fn=lambda *args: signals.append(args), token='test-token', projects_root='/tmp',
                          me='me', self_pid=500, explain_fn=fail)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, data = call(server.server_address[1], {'pids': [10]})
        assert status == 503 and 'retry' in data['error'] and not signals
    finally:
        server.shutdown(); server.server_close()
