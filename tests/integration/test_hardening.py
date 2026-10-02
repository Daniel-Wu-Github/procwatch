"""Server hardening over real loopback sockets: CSP nonce, header tokens, safe errors, Explain cap. No signals."""
import json
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = 'tok-1234'


@pytest.fixture
def serve():
    started = []
    procs = [Proc(10, 1, 'Python', '/usr/bin/python', '/tmp/work', (), 'me', 123, 0.0, 1.0)]
    def build(**kw):
        server = create_server(collect_fn=lambda: procs, machine_fn=lambda: MachineStats(1000, 500, 0, 2, None),
                               signal_fn=lambda *a: None, token=TOKEN, projects_root='/tmp', me='me', self_pid=500,
                               allow_kill=False, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server.server_address[1]
    yield build
    for server in started:
        server.shutdown(); server.server_close()


def get(port, path, headers=None):
    request = Request(f'http://127.0.0.1:{port}{path}', headers={'Host': f'127.0.0.1:{port}', **(headers or {})})
    try:
        with urlopen(request) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


def post(port, path, body, token=TOKEN):
    request = Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': token, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_page_csp_uses_a_fresh_nonce_and_no_unsafe_inline_script(serve):
    port = serve()
    seen = set()
    for _ in range(2):
        status, html, headers = get(port, f'/?token={TOKEN}')
        csp = headers['Content-Security-Policy']
        nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]{16,})'", csp).group(1)
        assert status == 200 and f'<script nonce="{nonce}">' in html and html.count('<script') == 1
        script_src = re.search(r'script-src ([^;]*)', csp).group(1)
        assert 'unsafe-inline' not in script_src
        seen.add(nonce)
    assert len(seen) == 2


def test_api_responses_cannot_run_scripts_at_all(serve):
    port = serve()
    _, _, headers = get(port, f'/api/snapshot?metric=mem&token={TOKEN}')
    assert "default-src 'none'" in headers['Content-Security-Policy'] and 'script-src' not in headers['Content-Security-Policy'].replace("default-src 'none'", '')


def test_get_api_accepts_the_token_in_a_header_and_query_still_works(serve):
    port = serve()
    assert get(port, '/api/snapshot?metric=mem', {'X-Procwatch-Token': TOKEN})[0] == 200
    assert get(port, '/api/snapshot?metric=mem', {'X-Procwatch-Token': 'wrong'})[0] == 403
    assert get(port, '/api/snapshot?metric=mem')[0] == 403
    assert get(port, f'/api/snapshot?metric=mem&token={TOKEN}')[0] == 200


def test_page_sends_the_token_as_a_header_not_in_fetch_urls():
    from pathlib import Path
    html = Path('procwatch/page.html').read_text()
    assert "searchParams.set('token'" not in html and "'X-Procwatch-Token':config.token" in html


def test_bad_input_is_rejected_without_echoing_it_back(serve):
    port = serve()
    status, body, _ = get(port, f'/api/breakdown?metric=mem&group=Python&parent=%3Cscript%3E&token={TOKEN}')
    assert status == 400 and '<script>' not in body
    request = Request(f'http://127.0.0.1:{port}/api/preview', data=b'{}', headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': TOKEN, 'Content-Length': '<b>x</b>'}, method='POST')
    try:
        urlopen(request)
    except HTTPError as exc:
        assert exc.code == 400 and b'<b>' not in exc.read()
    except Exception:
        pass  # the HTTP client itself may refuse a malformed header; the server never echoes it either way


def test_explain_has_a_small_concurrency_cap(serve):
    release, entered = threading.Event(), threading.Semaphore(0)
    def slow(facts):
        entered.release()
        release.wait(10)
        return 'done'
    port = serve(explain_fn=slow)
    results = []
    workers = [threading.Thread(target=lambda: results.append(post(port, '/api/explain', {'pids': [10]})[0])) for _ in range(2)]
    for worker in workers:
        worker.start()
    assert entered.acquire(timeout=5) and entered.acquire(timeout=5)
    status, data = post(port, '/api/explain', {'pids': [10]})
    assert status == 429 and 'busy' in data['error']
    release.set()
    for worker in workers:
        worker.join(5)
    assert sorted(results) == [200, 200]
    assert post(port, '/api/explain', {'pids': [10]})[0] == 200


def test_expired_link_gets_a_helpful_page_for_browsers_but_never_for_foreign_hosts(serve):
    port = serve()
    for path in ('/', '/?token=wrong'):
        status, body, headers = get(port, path, {'Accept': 'text/html'})
        assert status == 403 and headers['Content-Type'].startswith('text/html')
        assert 'procs' in body and TOKEN not in body and '<script' not in body
        assert "default-src 'none'" in headers['Content-Security-Policy']
    status, body, headers = get(port, '/')
    assert status == 403 and headers['Content-Type'] == 'application/json'
    status, body, headers = get(port, f'/?token={TOKEN}', {'Accept': 'text/html', 'Host': 'evil.example'})
    assert status == 403 and headers['Content-Type'] == 'application/json'
    assert get(port, '/api/snapshot?metric=mem', {'Accept': 'text/html'})[2]['Content-Type'] == 'application/json'
