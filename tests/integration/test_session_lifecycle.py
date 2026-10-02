"""One procwatch session per terminal: Ctrl-C, closing the terminal, or closing the browser tab all stop it cleanly.
Uses a real `procs` subprocess (read-only table, only our own child is ever signalled) and real loopback sockets."""
import http.server
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from procwatch.instance import Instance
from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = 'tok-1234'


# ------------------------------------------------------------------ in-process server (tab-close protocol)
@pytest.fixture
def session():
    started = []
    procs = [Proc(10, 1, 'Python', '/usr/bin/python', '/tmp/work', (), 'me', 123, 0.0, 1.0)]
    def build(grace=0.3):
        server = create_server(collect_fn=lambda: procs, machine_fn=lambda: MachineStats(1000, 500, 0, 2, None),
                               signal_fn=lambda *a: None, token=TOKEN, projects_root='/tmp', me='me', self_pid=500,
                               allow_kill=False, close_grace=grace)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append(server)
        return server, thread
    yield build
    for server in started:
        try:
            server.shutdown()
        except Exception:
            pass
        server.server_close()


def open_page(port):
    host = f'127.0.0.1:{port}'
    with urlopen(Request(f'http://{host}/?token={TOKEN}', headers={'Host': host, 'Accept': 'text/html'})) as response:
        html = response.read().decode()
    return json.loads(re.search(r'const config=(\{.*?\});\n', html).group(1))['pageId']


def close_page(port, page_id, token=TOKEN):
    request = Request(f'http://127.0.0.1:{port}/api/closed', data=json.dumps({'id': page_id}).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': token, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def test_page_gets_a_unique_id_each_time_it_loads(session):
    server, _ = session()
    port = server.server_address[1]
    assert open_page(port) != open_page(port)


def test_closing_the_only_tab_stops_the_server_after_a_short_grace(session):
    server, thread = session(grace=0.3)
    port = server.server_address[1]
    page = open_page(port)
    assert close_page(port, page) == 200
    thread.join(3)
    assert not thread.is_alive()


def test_a_reload_inside_the_grace_period_keeps_the_session_alive(session):
    server, thread = session(grace=0.6)
    port = server.server_address[1]
    assert close_page(port, open_page(port)) == 200
    open_page(port)                      # the reloaded page registers before the grace period ends
    thread.join(1.5)
    assert thread.is_alive()


def test_closing_one_of_two_tabs_keeps_running_and_closing_both_stops(session):
    server, thread = session(grace=0.3)
    port = server.server_address[1]
    first, second = open_page(port), open_page(port)
    assert close_page(port, first) == 200
    thread.join(1.0)
    assert thread.is_alive()
    assert close_page(port, second) == 200
    thread.join(3)
    assert not thread.is_alive()


def test_close_needs_the_token_and_a_page_id_this_server_issued(session):
    server, thread = session(grace=0.2)
    port = server.server_address[1]
    page = open_page(port)
    assert close_page(port, page, token='wrong') == 403
    assert close_page(port, 'not-an-issued-id') == 400
    assert close_page(port, page) == 200 and close_page(port, page) == 400   # an id closes once
    thread.join(3)
    assert not thread.is_alive()


def test_a_server_nobody_opened_a_page_on_never_stops_itself(session):
    server, thread = session(grace=0.2)
    assert close_page(server.server_address[1], 'made-up') == 400
    thread.join(0.8)
    assert thread.is_alive()


def test_page_reports_its_own_closing_and_reloads_when_restored_from_cache():
    from pathlib import Path
    html = Path('procwatch/page.html').read_text()
    assert 'config.pageId' in html and "addEventListener('pagehide'" in html and 'keepalive:true' in html
    assert "addEventListener('pageshow'" in html and 'location.reload()' in html


# ------------------------------------------------------------------ real `procs` subprocess
def start_procs(tmp_path, *extra):
    env = dict(os.environ, HOME=str(tmp_path / 'home'), PROCWATCH_INSTANCE_DIR=str(tmp_path / 'lease'), PYTHONUNBUFFERED='1')
    (tmp_path / 'home').mkdir(exist_ok=True)
    child = subprocess.Popen([sys.executable, '-m', 'procwatch.cli', '--no-browser', '--read-only', *extra], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    ready, _, _ = select.select([child.stdout], [], [], 15)
    assert ready, 'procs did not print its URL'
    url = child.stdout.readline().strip()
    assert url.startswith('http://127.0.0.1:'), url
    return child, url


def stop_and_collect(child, how, timeout=5):
    child.send_signal(how)
    code = child.wait(timeout)
    return code, child.stdout.read()


@pytest.mark.parametrize('how', [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_ctrl_c_terminal_close_and_terminate_all_stop_cleanly_and_free_the_lease(tmp_path, how):
    child, url = start_procs(tmp_path)
    try:
        code, output = stop_and_collect(child, how)
    finally:
        if child.poll() is None:
            child.kill()
    assert code == 0 and 'procwatch stopped' in output
    with Instance(tmp_path / 'lease') as lease:
        assert lease.acquire()


def test_ctrl_c_does_not_wait_for_a_slow_explain_request(tmp_path):
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(30)
        def log_message(self, *args): pass
    upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    child, url = start_procs(tmp_path, '--explain-url', f'http://127.0.0.1:{upstream.server_address[1]}/v1')
    try:
        port = int(re.search(r':(\d+)/', url).group(1))
        token = url.split('token=')[1]
        request = Request(f'http://127.0.0.1:{port}/api/explain', data=json.dumps({'pids': [child.pid]}).encode(), headers={
            'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': token, 'Content-Type': 'application/json'})
        def slow_call():
            try:
                urlopen(request, timeout=40)
            except OSError:
                pass                                     # the server is stopped under it on purpose
        threading.Thread(target=slow_call, daemon=True).start()
        time.sleep(1.0)                                    # the slow upstream call is now in flight
        started = time.monotonic()
        code, _ = stop_and_collect(child, signal.SIGINT, timeout=8)
        assert code == 0 and time.monotonic() - started < 6
    finally:
        if child.poll() is None:
            child.kill()
        upstream.shutdown(); upstream.server_close()


def test_closing_the_browser_tab_stops_the_real_command(tmp_path):
    child, url = start_procs(tmp_path)
    try:
        port = int(re.search(r':(\d+)/', url).group(1))
        token = url.split('token=')[1]
        host = f'127.0.0.1:{port}'
        with urlopen(Request(url, headers={'Host': host, 'Accept': 'text/html'})) as response:
            page = json.loads(re.search(r'const config=(\{.*?\});\n', response.read().decode()).group(1))['pageId']
        assert close_page(port, page, token=token) == 200
        assert child.wait(10) == 0
        assert 'tab' in child.stdout.read().lower()
    finally:
        if child.poll() is None:
            child.kill()
