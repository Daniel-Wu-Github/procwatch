"""A brief process-table cache for read-only GETs; anything that signals always reads fresh."""
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = 'tok-1234'


class World:
    def __init__(self):
        self.collects = 0
        self.procs = [Proc(10, 1, 'app', '/Applications/A.app/Contents/MacOS/a', None, ('a',), 'me', 100, 1.0, 1.0)]

    def collect(self):
        self.collects += 1
        return list(self.procs)


@pytest.fixture
def serve():
    started = []
    def build(world, **kw):
        server = create_server(collect_fn=world.collect, machine_fn=lambda: MachineStats(1000, 500, 0, 2, None),
                               signal_fn=lambda *a: None, token=TOKEN, projects_root='/p', me='me', self_pid=500, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server.server_address[1]
    yield build
    for server in started:
        server.shutdown(); server.server_close()


def get(port, path='/api/snapshot?metric=mem'):
    request = Request(f'http://127.0.0.1:{port}{path}', headers={'Host': f'127.0.0.1:{port}', 'X-Procwatch-Token': TOKEN})
    with urlopen(request) as response:
        return json.loads(response.read())


def post(port, path, body):
    request = Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(), headers={
        'Origin': f'http://127.0.0.1:{port}', 'X-Procwatch-Token': TOKEN, 'Content-Type': 'application/json'})
    try:
        with urlopen(request) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        return json.loads(exc.read())


def test_no_cache_by_default_so_every_get_reads_the_table(serve):
    world = World()
    port = serve(world)
    get(port); get(port)
    assert world.collects == 2


def test_gets_within_the_ttl_share_one_read(serve):
    world = World()
    port = serve(world, table_ttl=5.0)
    get(port); get(port); get(port, '/api/snapshot?metric=cpu')
    assert world.collects == 1


def test_the_cache_expires(serve):
    world = World()
    port = serve(world, table_ttl=0.2)
    get(port)
    time.sleep(0.35)
    get(port)
    assert world.collects == 2


def test_preview_and_actions_never_use_the_cache(serve):
    world = World()
    port = serve(world, table_ttl=60.0, allow_kill=True)
    get(port)
    before = world.collects
    preview = post(port, '/api/preview', {'pids': [10]})
    assert world.collects == before + 1
    post(port, '/api/stop', {'confirmed': preview['targets']})
    assert world.collects == before + 2


def test_an_action_invalidates_the_cache_for_the_next_get(serve):
    world = World()
    port = serve(world, table_ttl=60.0, allow_kill=True)
    get(port)
    preview = post(port, '/api/preview', {'pids': [10]})
    post(port, '/api/stop', {'confirmed': preview['targets']})
    after_action = world.collects
    world.procs.clear()                                   # the stopped process is gone
    assert get(port)['procs'] == [] and world.collects == after_action + 1
