"""Explain wiring: config -> server -> page flags, over real loopback sockets. No inference, no signals."""
import json
import re
import threading
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from procwatch.config import ExplainConfig
from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = 'tok-1234'


class Upstream:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return b'{"choices":[{"message":{"content":"Configured answer."}}]}'


@pytest.fixture
def make():
    started, signals = [], []
    procs = [Proc(10, 1, 'Python', '/usr/bin/python', '/tmp/work', ('python', '--secret', 'DO_NOT_SEND'), 'me', 123, 0.0, 1.0)]
    def build(**kw):
        server = create_server(collect_fn=lambda: procs, machine_fn=lambda: MachineStats(1000, 500, 0, 2, None),
                               signal_fn=lambda *a: signals.append(a), token=TOKEN, projects_root='/tmp',
                               me='me', self_pid=500, allow_kill=False, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server.server_address[1]
    yield build, signals
    for server in started:
        server.shutdown(); server.server_close()


def page_config(port):
    host = f'127.0.0.1:{port}'
    with urlopen(Request(f'http://{host}/?token={TOKEN}', headers={'Host': host})) as response:
        html = response.read().decode()
    return json.loads(re.search(r'const config=(\{.*?\});\n', html).group(1))


def explain(port, body=None):
    request = Request(f'http://127.0.0.1:{port}/api/explain', data=json.dumps(body or {'pids': [10]}).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': TOKEN, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response: return response.status, json.loads(response.read())
    except HTTPError as exc: return exc.code, json.loads(exc.read())


def test_unconfigured_explain_is_off_in_the_page_and_refused_by_the_api(make):
    build, signals = make
    port = build()
    assert page_config(port)['explain'] == {'enabled': False, 'remoteHost': None}
    status, data = explain(port)
    assert status == 503 and 'not configured' in data['error'] and not signals


def test_configured_loopback_endpoint_is_used_and_redacted(make):
    build, signals = make
    port = build(explain_config=ExplainConfig(base_url='http://127.0.0.1:11434/v1', model='llama3.1'))
    assert page_config(port)['explain'] == {'enabled': True, 'remoteHost': None}
    with patch('procwatch.llm.urlopen', return_value=Upstream()) as upstream:
        status, data = explain(port)
    assert status == 200 and data['explanation'] == 'Configured answer.' and data['advisory']
    request = upstream.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:11434/v1/chat/completions'
    assert b'DO_NOT_SEND' not in request.data and json.loads(request.data)['model'] == 'llama3.1'
    assert not signals


def test_remote_endpoint_is_flagged_to_the_page_with_its_host(make):
    build, _ = make
    port = build(explain_config=ExplainConfig(base_url='https://api.example.com/v1', allow_remote=True))
    assert page_config(port)['explain'] == {'enabled': True, 'remoteHost': 'api.example.com'}


def test_an_injected_explain_function_still_enables_explain(make):
    build, _ = make
    port = build(explain_fn=lambda facts: 'Injected.')
    assert page_config(port)['explain']['enabled'] is True
    assert explain(port)[1]['explanation'] == 'Injected.'


def test_page_hides_explain_controls_when_disabled():
    from pathlib import Path
    html = Path('procwatch/page.html').read_text()
    assert 'config.explain.enabled' in html and 'config.explain.remoteHost' in html
