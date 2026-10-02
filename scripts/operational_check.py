"""Opt-in real shutdown QA. Targets only disposable processes this script creates."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import psutil

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()


def worker(mode, folder):
    folder = Path(folder)
    (folder / f'{mode}.pid').write_text(str(os.getpid()))
    child = None
    if mode in ('tree', 'branch'):
        next_mode = 'branch' if mode == 'tree' else 'leaf'
        child = subprocess.Popen([sys.executable, str(SCRIPT), '--worker', next_mode, str(folder)], cwd=folder)
    def term(sig, frame):
        (folder / f'{mode}.term').write_text('SIGTERM received')
        if mode == 'stubborn':
            return
        if child:
            child.wait(timeout=10)
        (folder / f'{mode}.done').write_text('clean exit')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, term)
    (folder / f'{mode}.ready').touch()
    while True:
        time.sleep(.05)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for operational invariant')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-real-signals', action='store_true')
    parser.add_argument('--browser', action='store_true', help='Leave three owned targets for Playwright; finish via the printed session folder')
    args = parser.parse_args()
    if not args.allow_real_signals:
        parser.error('Real signals require --allow-real-signals; targets are created and owned by this script')
    run = ROOT / 'artifacts' / ('operational-' + str(time.time_ns()))
    projects = run / 'projects'
    projects.mkdir(parents=True)
    rules = run / 'empty.toml'
    rules.write_text('')
    processes = {}
    identities = {}
    checks = []
    server = None
    def record(name, **details):
        checks.append(dict(check=name, passed=True, **details))
        print('PASS: ' + name, flush=True)
        (run / 'results.json').write_text(json.dumps(checks, indent=2))
    def start(name, mode='graceful'):
        folder = projects / name
        folder.mkdir()
        p = subprocess.Popen([sys.executable, str(SCRIPT), '--worker', mode, str(folder)], cwd=folder)
        processes[name] = (p, folder, mode)
        wait_for(lambda: (folder / f'{mode}.ready').exists())
        identities[p.pid] = psutil.Process(p.pid).create_time()
        return p
    def alive(p):
        return p.poll() is None
    try:
        server = subprocess.Popen([str(ROOT / '.venv/bin/procs'), '--no-browser', '--rules', str(rules),
                                   '--projects-root', str(projects)], cwd=ROOT, stdout=subprocess.PIPE,
                                  env={**os.environ, 'PROCWATCH_INSTANCE_DIR': str(run/'dashboard-state')},
                                  stderr=(run/'server-errors.txt').open('w'), text=True)
        url = server.stdout.readline().strip()
        parsed = urlsplit(url)
        assert parsed.hostname == '127.0.0.1' and parsed.port
        base = f'http://127.0.0.1:{parsed.port}'
        token = parse_qs(parsed.query)['token'][0]
        def call(path, body=None, headers=None, authenticated=True):
            h = dict(headers or {})
            if body is None:
                if authenticated:
                    path += ('&' if '?' in path else '?') + 'token=' + token
            else:
                h.setdefault('Content-Type', 'application/json')
                h.setdefault('Origin', base)
                if authenticated:
                    h.setdefault('X-Procwatch-Token', token)
                selected = body.get('pids', []) if isinstance(body, dict) else []
                if isinstance(selected, list):
                    assert all(type(pid) is int and pid in identities for pid in selected)
                if isinstance(body, dict) and isinstance(body.get('groups', []), list):
                    assert all(g in processes for g in body.get('groups', []))
            req = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=h, method='POST' if body is not None else 'GET')
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    return response.status, json.loads(response.read()), response.headers
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read()), exc.headers
        control = start('qa-control')
        graceful = start('qa-graceful')
        stubborn = start('qa-stubborn', 'stubborn')
        forced = start('qa-force')
        tree = start('qa-tree', 'tree')
        tree_folder = processes['qa-tree'][1]
        wait_for(lambda: (tree_folder/'leaf.ready').exists())
        tree_pids = [int((tree_folder/f'{name}.pid').read_text()) for name in ('tree','branch','leaf')]
        for pid in tree_pids:
            identities[pid] = psutil.Process(pid).create_time()
        status, snapshot, headers = call('/api/snapshot')
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        actual = {p['pid']:p for p in snapshot['procs']}
        assert actual[graceful.pid]['group'] == 'qa-graceful'
        assert actual[server.pid]['protected']
        record('CLI live server binds loopback, authenticates, and protects itself')
        for name, kwargs in [('missing token', dict(authenticated=False)),
                             ('foreign origin', dict(headers={'Origin':'http://evil.example'})),
                             ('foreign host', dict(headers={'Host':'evil.example'}))]:
            status, _, _ = call('/api/stop', {'pids':[control.pid]}, **kwargs)
            assert status == 403 and alive(control)
            record(name+' rejected without signalling owned control')
        for body in ({}, {'pids':'bad'}):
            status, _, _ = call('/api/stop', body)
            assert status == 400 and alive(control)
        record('Malformed action bodies rejected without signalling control')
        status, preview, _ = call('/api/preview', {'pids':[graceful.pid]})
        assert status == 200 and preview['pids'] == [graceful.pid] and alive(graceful)
        record('Preview returns owned target and sends no signal')
        status, data, _ = call('/api/stop', {'pids':[graceful.pid]})
        assert status == 200 and data['outcomes'] == [{'pid':graceful.pid,'status':'signalled'}]
        assert graceful.wait(timeout=10) == 0 and (processes['qa-graceful'][1]/'graceful.done').exists()
        record('Real Stop delivers SIGTERM and permits graceful cleanup', pid=graceful.pid, returncode=0)
        status, data, _ = call('/api/force', {'pids':[forced.pid]})
        assert status == 200 and forced.wait(timeout=10) == -signal.SIGKILL
        assert not (processes['qa-force'][1]/'graceful.done').exists()
        record('Real Force delivers SIGKILL without graceful cleanup', pid=forced.pid, returncode=-9)
        status, _, _ = call('/api/stop', {'pids':[stubborn.pid]})
        assert status == 200
        wait_for(lambda: (processes['qa-stubborn'][1]/'stubborn.term').exists())
        time.sleep(5)
        assert alive(stubborn)
        record('SIGTERM-ignoring target survives Stop for 5 seconds; no automatic escalation')
        status, _, _ = call('/api/force', {'pids':[stubborn.pid]})
        assert status == 200 and stubborn.wait(timeout=10) == -signal.SIGKILL
        record('Separate explicit Force terminates SIGTERM-ignoring target')
        status, preview, _ = call('/api/preview', {'groups':['qa-tree'], 'pids':[tree.pid]})
        assert status == 200 and preview['pids'] == list(reversed(tree_pids))
        status, data, _ = call('/api/stop', {'groups':['qa-tree'], 'pids':[tree.pid]})
        assert status == 200 and [o['pid'] for o in data['outcomes']] == list(reversed(tree_pids))
        assert all(o['status']=='signalled' for o in data['outcomes'])
        assert tree.wait(timeout=10) == 0
        assert all((tree_folder/f'{name}.done').exists() for name in ('tree','branch','leaf'))
        record('Group plus PID deduplicates a three-level tree and stops children first', order=list(reversed(tree_pids)))
        status, data, _ = call('/api/stop', {'pids':[graceful.pid]})
        assert status == 200 and not data['outcomes'] and data['refused'][0]['pid']==graceful.pid
        assert alive(control)
        record('Exited target is refused; unrelated disposable control survives')
        if args.browser:
            stop = start('qa-browser-stop')
            force = start('qa-browser-force', 'stubborn')
            session = dict(url=url, folder=str(run), stop_pid=stop.pid, force_pid=force.pid, control_pid=control.pid)
            (ROOT/'artifacts/operational-session.json').write_text(json.dumps(session, indent=2))
            print(json.dumps(session), flush=True)
            deadline = time.monotonic()+1800
            while not (run/'finish-browser').exists():
                (run/'browser-state.json').write_text(json.dumps({name:dict(pid=p.pid,returncode=p.poll(),term=(folder/f'{mode}.term').exists(),done=(folder/f'{mode}.done').exists()) for name,(p,folder,mode) in processes.items()},indent=2))
                if time.monotonic()>deadline:
                    raise TimeoutError('Browser QA exceeded 30 minutes')
                time.sleep(.2)
            assert stop.wait(timeout=10)==0 and (processes['qa-browser-stop'][1]/'graceful.done').exists()
            assert force.wait(timeout=10)==-signal.SIGKILL and (processes['qa-browser-force'][1]/'stubborn.term').exists()
            assert alive(control)
            record('Playwright real Stop/Force: graceful target exited, stubborn target terminated separately, control alive')
        record('All live operational assertions passed')
    finally:
        for pid, started in identities.items():
            try:
                p = psutil.Process(pid)
                if p.create_time()==started and p.is_running():
                    p.kill()
            except psutil.NoSuchProcess:
                pass
        for p, _, _ in processes.values():
            p.wait(timeout=10)
        if server:
            server.send_signal(signal.SIGINT)
            assert server.wait(timeout=10)==0
            record('CLI Ctrl-C shuts down cleanly')
            import socket
            with socket.socket() as sock:
                assert sock.connect_ex(('127.0.0.1', parsed.port)) != 0
            record('Listening socket closes after shutdown; disposable processes cleaned up')
        print('Operational results: '+str(run/'results.json'),flush=True)


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        worker(sys.argv[2],sys.argv[3])
    else:
        main()
