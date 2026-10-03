from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .config import ExplainConfig
from .grouping import classify
from .killer import StopPlan, Target, execute, plan
from .llm import ExplanationUnavailable, explain_processes, process_facts
from .model import ancestors
from .safety import check
from .security import request_allowed

def parse_confirmed(body):
    if set(body) != {'confirmed'}:
        raise ValueError('A confirmed list cannot be combined with pids or groups')
    items = body['confirmed']
    if not isinstance(items, list) or not 1 <= len(items) <= 2000:
        raise ValueError('confirmed must be a list of 1 to 2000 targets')
    result, seen = [], set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'pid', 'create_time'} or type(item['pid']) is not int
                or item['pid'] < 0 or type(item['create_time']) not in (int, float) or not math.isfinite(item['create_time'])):
            raise ValueError('Each confirmed target needs an integer pid and a numeric create_time')
        if item['pid'] not in seen:
            seen.add(item['pid'])
            result.append((item['pid'], float(item['create_time'])))
    return result


EXPIRED_PAGE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>procwatch · link expired</title>'
                '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
                '<body style="font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem">'
                '<h1>This procwatch link has expired</h1>'
                '<p>The token in this link is missing or no longer valid, which happens when procwatch was stopped or restarted.</p>'
                '<p>Run <code>procs</code> again in your terminal to open a fresh link.</p></body></html>')
from .snapshot import breakdown, build_snapshot, freed, origin


def create_server(*, collect_fn, machine_fn, signal_fn, token, projects_root, rules=(), me,
                  self_pid, allow_kill=True, host='127.0.0.1', port=0, explain_fn=None, explain_config=None, close_grace=3.0,
                  startup_ancestors=frozenset(), table_ttl=0.0):
    if host != '127.0.0.1':
        raise ValueError('procwatch must bind to 127.0.0.1')
    action_lock = threading.Lock()
    explain_slots = threading.BoundedSemaphore(2)
    pages, session = {}, {'timer': None, 'server': None}   # open page ids; closing the last one ends the session
    session_lock = threading.Lock()
    table_cache, cache_lock = {'at': 0.0, 'procs': None}, threading.Lock()   # read-only GETs may share one read for table_ttl seconds

    def cached_collect():
        with cache_lock:
            if table_cache['procs'] is None or time.monotonic() - table_cache['at'] >= table_ttl:
                table_cache['procs'] = collect_fn()
                table_cache['at'] = time.monotonic()
            return table_cache['procs']

    def drop_cache():
        with cache_lock:
            table_cache['procs'] = None

    def register_page():
        page_id = secrets.token_urlsafe(8)
        with session_lock:
            if session['timer']:
                session['timer'].cancel()
                session['timer'] = None
            pages[page_id] = True
            while len(pages) > 50:                       # a crashed tab never reports closing; keep this bounded
                pages.pop(next(iter(pages)))
        return page_id

    def stop_session():
        with session_lock:
            if pages:
                return
            session['server'].closed_by_tab = True
        threading.Thread(target=session['server'].shutdown, daemon=True).start()

    def close_page(page_id):
        with session_lock:
            if page_id not in pages:
                return False
            del pages[page_id]
            if not pages:
                session['timer'] = threading.Timer(close_grace, stop_session)
                session['timer'].daemon = True
                session['timer'].start()
        return True
    explain_config = explain_config or ExplainConfig()
    explain_enabled = explain_fn is not None or explain_config.enabled
    explain_remote_host = explain_config.host if explain_config.remote else None
    if explain_fn is None:
        explain_fn = lambda facts: explain_processes(facts, explain_config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def respond(self, status, body, html=False, nonce=None):
            payload = body.encode() if html else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            script = f"script-src 'nonce-{nonce}'; " if nonce else ''
            self.send_header('Content-Security-Policy', f"default-src 'none'; {script}style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def dispatch(self):
            try:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                supplied = (query.get('token', [None])[0] if self.command == 'GET' else None) or self.headers.get('X-Procwatch-Token')
                if any(len(self.headers.get_all(k, [])) > 1 for k in ('Host', 'Origin', 'X-Procwatch-Token')) or not request_allowed(
                        self.command, self.headers, supplied, expected_token=token, port=self.server.server_address[1]):
                    if (self.command == 'GET' and parsed.path == '/' and 'text/html' in self.headers.get('Accept', '')
                            and len(self.headers.get_all('Host', [])) == 1 and request_allowed(
                                'GET', self.headers, token, expected_token=token, port=self.server.server_address[1])):
                        self.respond(403, EXPIRED_PAGE, html=True)  # host is genuine: only the token is stale
                        return
                    self.respond(403, {'error': 'Forbidden'})
                    return
                if self.command == 'GET':
                    self.get(parsed.path, query)
                else:
                    self.post(parsed.path)
            except (ValueError, TypeError, UnicodeError) as exc:
                self.respond(400, {'error': str(exc)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception:
                self.respond(500, {'error': 'Unable to read process data; try again'})

        do_GET = dispatch
        do_POST = dispatch

        def table(self, fresh=False):
            procs = collect_fn() if fresh or not table_ttl else cached_collect()   # anything that signals reads fresh
            labels = classify(procs, rules, projects_root)
            # the chain captured at startup still counts if a parent vanished from the live table
            safety = dict(me=me, self_pid=self_pid, ancestors_of_self=ancestors(self_pid, procs) | startup_ancestors)
            return procs, labels, safety

        def get(self, path, query):
            if path == '/':
                config = json.dumps({'token': token, 'readOnly': not allow_kill, 'pageId': register_page(),
                                     'explain': {'enabled': explain_enabled, 'remoteHost': explain_remote_host}}).replace('<', '\\u003c')
                page = Path(__file__).with_name('page.html').read_text()
                nonce = secrets.token_urlsafe(16)
                page = page.replace('<script>', f'<script nonce="{nonce}">').replace('__PROCWATCH_CONFIG__', config)
                self.respond(200, page, html=True, nonce=nonce)
                return
            if path not in ('/api/snapshot', '/api/breakdown'):
                self.respond(404, {'error': 'Not found'})
                return
            metric = query.get('metric', ['mem'])[0]
            if metric not in ('mem', 'cpu', 'gpu'):
                raise ValueError('Unknown metric')
            procs, labels, safety = self.table()
            stats = machine_fn()
            if path == '/api/breakdown':
                parent = query.get('parent', [None])[0]
                if parent is not None and not (parent.isascii() and parent.isdigit()):
                    raise ValueError('parent must be a process id')
                result = breakdown(procs, labels, stats, metric=metric, group=query.get('group', [''])[0],
                                   parent_pid=int(parent) if parent is not None else None)
            else:
                rows = []
                for p in procs:
                    verdict = check(p, **safety)
                    rows.append(dict(pid=p.pid, ppid=p.ppid, name=p.name, group=labels[p.pid].name,
                                     kind=labels[p.pid].kind, rss=p.rss, cpu=p.cpu, origin=origin(p, procs),
                                     protected=not verdict.allowed, reason=verdict.reason))
                result = dict(snapshot=build_snapshot(procs, labels, stats, metric=metric), machine=asdict(stats), procs=rows)
            self.respond(200, result)

        def confirmed_action(self, path, confirmed):
            """Signal exactly the pids (with start times) the user confirmed: no re-planning, no new children."""
            with action_lock:
                procs, labels, safety = self.table(fresh=True)
                table = {p.pid: p for p in procs}
                order, refused = [], []
                for pid, started in confirmed:
                    p = table.get(pid)
                    verdict = check(p, **safety) if p is not None else None
                    if p is None and (pid <= 1 or pid == self_pid):      # never even attempted, present or not
                        refused.append((pid, 'Protected system pid' if pid <= 1 else 'This is procwatch itself'))
                    elif verdict is not None and not verdict.allowed:
                        refused.append((pid, verdict.reason))
                    else:
                        order.append(Target(pid, started))   # a missing pid is reported 'gone', a changed start 'reused'
                outcomes = execute(StopPlan(tuple(order), tuple(refused)), signal.SIGTERM if path == '/api/stop' else signal.SIGKILL,
                                   signal_fn=signal_fn, create_time_of=lambda pid: table[pid].create_time if pid in table else None)
                drop_cache()
            self.respond(200, dict(outcomes=[asdict(o) for o in outcomes],
                                   refused=[dict(pid=pid, reason=reason) for pid, reason in refused]))

        def post(self, path):
            if path not in ('/api/preview', '/api/stop', '/api/force', '/api/explain', '/api/closed'):
                self.respond(404, {'error': 'Not found'})
                return
            if path in ('/api/stop', '/api/force') and not allow_kill:
                self.respond(403, {'error': 'read-only mode: signals are disabled'})
                return
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                raise ValueError('Expected a Content-Length')
            length = self.headers.get('Content-Length', '0')
            if not (length.isascii() and length.isdigit()):
                raise ValueError('Content-Length must be a number')
            size = int(length)
            if not 0 < size <= 65536:
                raise ValueError('Body must be between 1 and 65536 bytes')
            body = json.loads(self.rfile.read(size))
            if path == '/api/closed':
                page_id = body.get('id') if isinstance(body, dict) and set(body) == {'id'} else None
                if not isinstance(page_id, str) or not close_page(page_id):
                    raise ValueError('Unknown page')
                self.respond(200, {'closing': True})
                return
            if path in ('/api/stop', '/api/force') and isinstance(body, dict) and 'confirmed' in body:
                self.confirmed_action(path, parse_confirmed(body))
                return
            if not isinstance(body, dict) or set(body) - {'pids', 'groups'}:
                raise ValueError('Expected pids and/or groups')
            pids, groups = body.get('pids', []), body.get('groups', [])
            if (not isinstance(pids, list) or not isinstance(groups, list)
                    or any(type(pid) is not int or pid < 0 for pid in pids)
                    or any(not isinstance(g, str) or not g for g in groups) or not (pids or groups)):
                raise ValueError('Select at least one pid or group')
            if path == '/api/explain':
                if not explain_enabled:
                    self.respond(503, {'error': 'Explain is not configured. Set [explain] base_url in config.toml (see the README).'})
                    return
                procs, labels, safety = self.table()
                if set(groups) - {label.name for label in labels.values()}:
                    raise ValueError('Unknown group')
                wanted = set(pids) | {pid for pid, label in labels.items() if label.name in groups}
                selected = [p for p in procs if p.pid in wanted]
                if not selected:
                    raise ValueError('Selected processes have exited; select again')
                if len(selected) > 12:
                    raise ValueError('Explain up to 12 processes at a time; select a smaller group')
                facts = [process_facts(p, labels[p.pid], check(p, **safety)) for p in selected]
                if not explain_slots.acquire(blocking=False):
                    self.respond(429, {'error': 'Explain is busy with other requests; retry shortly.'})
                    return
                try:
                    text = explain_fn(facts)
                except ExplanationUnavailable as exc:
                    self.respond(503, {'error': str(exc)})
                    return
                finally:
                    explain_slots.release()
                self.respond(200, dict(explanation=text, pids=[p.pid for p in selected], advisory=True))
                return
            if path in ('/api/stop', '/api/force'):
                raise ValueError('Stop and Force need the confirmed list returned by a preview')
            with action_lock:
                procs, labels, safety = self.table(fresh=True)
                if set(groups) - {label.name for label in labels.values()}:
                    raise ValueError('Unknown group')
                selected = pids + [pid for pid, label in labels.items() if label.name in groups]
                planned = plan(selected, procs, **safety)
                refused = [dict(pid=pid, reason=reason) for pid, reason in planned.refused]
                targets = [target.pid for target in planned.order]
                result = dict(pids=targets, refused=refused, frees=freed(targets, procs, machine_fn()),
                              targets=[dict(pid=t.pid, create_time=t.create_time) for t in planned.order])
            self.respond(200, result)

    httpd = ThreadingHTTPServer((host, port), Handler)

    def wait_for_actions(timeout):
        """Block until an in-flight Stop/Force has finished (or the timeout passes)."""
        if action_lock.acquire(timeout=timeout):
            action_lock.release()
            return True
        return False
    httpd.wait_for_actions = wait_for_actions
    httpd.daemon_threads = True      # Ctrl-C must not wait for a slow Explain request
    httpd.block_on_close = False
    httpd.closed_by_tab = False
    session['server'] = httpd
    return httpd
