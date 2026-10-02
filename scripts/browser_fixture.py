"""Local browser QA with fictional processes and recording-only signals."""
from pathlib import Path
import sys
import secrets
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from procwatch.model import MachineStats, Proc
from procwatch.server import create_server


def proc(pid, parent, name, group, rss, cpu=10, owner='fixture'):
    return Proc(pid, parent, name, '/Applications/'+group+'.app/Contents/MacOS/'+name,
                None, (name, '--fixture'), owner, rss*1024**2, cpu, float(pid))


processes = [proc(100, 1, 'Studio', 'Studio', 1000),
             proc(101, 100, 'Renderer', 'Studio', 500),
             proc(102, 101, 'Worker', 'Studio', 250),
             proc(103, 100, '<img src=x onerror=alert(1)>', 'Studio', 50),
             proc(200, 1, 'Editor', 'Editor', 400),
             proc(300, 1, 'Finder', 'macOS', 200),
             proc(400, 1, 'Database', 'Database', 300, owner='root')]
machine = MachineStats(8*1024**3, 4*1024**3, 35.0, 8, 22.0)
calls = []
fixture_token = secrets.token_urlsafe(32)
server = create_server(collect_fn=lambda: processes, machine_fn=lambda: machine,
                       signal_fn=lambda pid, sig: calls.append((pid, sig)), token=fixture_token,
                       projects_root='/fixture/projects', me='fixture', self_pid=999, port=0)
original_respond = server.RequestHandlerClass.respond


def fixture_respond(self, status, body, html=False, nonce=None):
    if html:
        body = body.replace('<title>procwatch', '<title>TEST FIXTURE · procwatch')
        body = body.replace('<h1>procwatch</h1>', '<h1>procwatch · TEST FIXTURE</h1>')
        body = body.replace('Make room for what’s next.', 'Simulated data for UX testing.')
        body = body.replace('See what’s running. Choose what can take a break.',
                            'Mock 8 GB machine · fictional processes · no real signals.')
    return original_respond(self, status, body, html=html, nonce=nonce)


server.RequestHandlerClass.respond = fixture_respond
url = f'http://127.0.0.1:{server.server_address[1]}/?token={fixture_token}'
artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
artifacts.mkdir(exist_ok=True)
(artifacts / 'browser-fixture-url.txt').write_text(url)
print(url, flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
    print(f'Recorded signals: {calls}', flush=True)
