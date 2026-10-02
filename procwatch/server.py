from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import threading
from urllib.parse import parse_qs, urlsplit

from .config import ExplainConfig
from .grouping import classify
from .killer import execute, plan
from .llm import ExplanationUnavailable, explain_processes, process_facts
from .model import ancestors
from .safety import check
from .security import request_allowed
from .snapshot import breakdown, build_snapshot, freed, origin


def create_server(*, collect_fn, machine_fn, signal_fn, token, projects_root, rules=(), me,
                  self_pid, allow_kill=True, host='127.0.0.1', port=0, explain_fn=None, explain_config=None):
    if host != '127.0.0.1':
        raise ValueError('procwatch must bind to 127.0.0.1')
    action_lock = threading.Lock()
    explain_slots = threading.BoundedSemaphore(2)
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

        def table(self):
            procs = collect_fn()
            labels = classify(procs, rules, projects_root)
            safety = dict(me=me, self_pid=self_pid, ancestors_of_self=ancestors(self_pid, procs))
            return procs, labels, safety

        def get(self, path, query):
            if path == '/':
                config = json.dumps({'token': token, 'readOnly': not allow_kill,
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

        def post(self, path):
            if path not in ('/api/preview', '/api/stop', '/api/force', '/api/explain'):
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
            with action_lock:
                procs, labels, safety = self.table()
                if set(groups) - {label.name for label in labels.values()}:
                    raise ValueError('Unknown group')
                selected = pids + [pid for pid, label in labels.items() if label.name in groups]
                planned = plan(selected, procs, **safety)
                refused = [dict(pid=pid, reason=reason) for pid, reason in planned.refused]
                if path == '/api/preview':
                    targets = [target.pid for target in planned.order]
                    result = dict(pids=targets, refused=refused, frees=freed(targets, procs, machine_fn()))
                else:
                    def create_time_of(pid):
                        fresh = collect_fn()
                        return next((p.create_time for p in fresh if p.pid == pid), None)
                    outcomes = execute(planned, signal.SIGTERM if path == '/api/stop' else signal.SIGKILL,
                                       signal_fn=signal_fn, create_time_of=create_time_of)
                    result = dict(outcomes=[asdict(o) for o in outcomes], refused=refused)
            self.respond(200, result)

    return ThreadingHTTPServer((host, port), Handler)
