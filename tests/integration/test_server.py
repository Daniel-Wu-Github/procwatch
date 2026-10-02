"""The HTTP surface, over real sockets on 127.0.0.1 with fake processes and a
recording signal function — nothing real is ever signalled."""

import json
import signal
import threading
import urllib.error
import urllib.request

import pytest

from procwatch.model import MachineStats, Proc
from procwatch.server import create_server

TOKEN = "t0ken"
ROOT = "/Users/tester/projects"


def P(pid, ppid=1, name="thing", exe=None, cwd=None, cmdline=(), username="tester", rss=0, cpu=0.0, create_time=1.0):
    return Proc(pid, ppid, name, exe, cwd, tuple(cmdline), username, rss, cpu, create_time)


PROCS = [
    P(1, 0, "launchd", "/sbin/launchd", username="root"),
    P(500, 400, "procwatch", "/opt/homebrew/bin/python3"),                 # this tool
    P(400, 1, "zsh", "/bin/zsh"),                                          # its shell
    P(900, 400, "vite", "/opt/homebrew/bin/node", f"{ROOT}/web-app/web", ("node", "vite"), rss=200 * 1024 * 1024),
    P(901, 900, "esbuild", None, None, (), rss=50 * 1024 * 1024, create_time=2.0),
    P(950, 1, "Vivaldi", "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi", rss=800 * 1024 * 1024, create_time=3.0),
    P(960, 1, "Dock", "/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock", rss=60 * 1024 * 1024),
]


MB = 1024 * 1024
MACH = MachineStats(mem_total=2000 * MB, mem_available=1000 * MB, cpu_busy_pct=40.0, cpu_cores=8, gpu_busy_pct=25.0)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, pid, sig):
        self.calls.append((pid, sig))


@pytest.fixture
def served():
    recorder = Recorder()
    server = create_server(collect_fn=lambda: list(PROCS), machine_fn=lambda: MACH, signal_fn=recorder, token=TOKEN, projects_root=ROOT,
                           me="tester", self_pid=500, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, recorder, server
    server.shutdown()
    server.server_close()


def call(port, path, method="GET", body=None, headers=None, origin=True, token_header=True):
    h = dict(headers or {})
    if method == "POST":
        h.setdefault("Content-Type", "application/json")
        if origin:
            h.setdefault("Origin", f"http://127.0.0.1:{port}")
        if token_header:
            h.setdefault("X-Procwatch-Token", TOKEN)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def snapshot(port, metric="mem"):
    status, body, _ = call(port, f"/api/snapshot?metric={metric}&token={TOKEN}")
    assert status == 200
    return json.loads(body)


# ------------------------------------------------------------------ page & auth

def test_the_page_is_served_as_html_only_with_the_token(served):
    port, _, _ = served
    status, body, headers = call(port, f"/?token={TOKEN}")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert TOKEN.encode() in body                                  # the page embeds the token for its own fetches
    assert call(port, "/")[0] == 403
    assert call(port, "/?token=nope")[0] == 403


def test_a_foreign_host_header_is_rejected_even_with_the_token(served):
    port, _, _ = served
    assert call(port, f"/api/snapshot?token={TOKEN}", headers={"Host": "evil.example.com"})[0] == 403


def test_unknown_paths_are_404_with_a_valid_token(served):
    port, _, _ = served
    assert call(port, f"/nope?token={TOKEN}")[0] == 404


def test_the_server_binds_to_loopback_only(served):
    _, _, server = served
    assert server.server_address[0] == "127.0.0.1"


# ------------------------------------------------------------------ snapshot

def test_snapshot_groups_processes_and_marks_protected_ones(served):
    port, _, _ = served
    data = snapshot(port)
    labels = {s["label"] for s in data["snapshot"]["slices"]}
    assert {"web-app", "Vivaldi"} <= labels
    by_pid = {p["pid"]: p for p in data["procs"]}
    assert by_pid[900]["group"] == "web-app" and by_pid[900]["kind"] == "project"
    assert by_pid[901]["group"] == "web-app" and by_pid[901]["kind"] == "inherited"
    assert by_pid[950]["origin"]["exe"].endswith("Vivaldi.app/Contents/MacOS/Vivaldi")
    assert by_pid[900]["origin"]["parents"][0] == {"pid": 400, "name": "zsh"}
    assert by_pid[900]["protected"] is False
    for pid in (1, 500, 400, 960):                                  # root, this tool, its shell, Dock
        assert by_pid[pid]["protected"] is True and by_pid[pid]["reason"]


def test_the_pie_is_the_whole_machine_with_a_free_slice_and_machine_stats(served):
    port, _, _ = served
    data = snapshot(port)
    s = data["snapshot"]
    assert s["total"] == MACH.mem_total and s["slices"][-1]["label"] == "Free"
    assert sum(x["share"] for x in s["slices"]) == pytest.approx(100.0, abs=0.01)
    assert data["machine"] == {"mem_total": MACH.mem_total, "mem_available": MACH.mem_available,
                               "cpu_busy_pct": 40.0, "cpu_cores": 8, "gpu_busy_pct": 25.0}


def test_snapshot_supports_cpu_and_gpu_and_rejects_an_unknown_metric(served):
    port, _, _ = served
    cpu = snapshot(port, "cpu")["snapshot"]
    assert cpu["metric"] == "cpu" and cpu["slices"][-1]["label"] == "Idle"
    gpu = snapshot(port, "gpu")["snapshot"]
    assert [x["label"] for x in gpu["slices"]] == ["GPU busy", "Idle"] and gpu["groups"] == {}
    assert call(port, f"/api/snapshot?metric=disk&token={TOKEN}")[0] == 400


# ------------------------------------------------------------------ drill-down

def breakdown(port, query):
    return call(port, f"/api/breakdown?{query}&token={TOKEN}")


def test_breakdown_lists_a_groups_roots_then_a_processs_children_and_itself(served):
    port, _, _ = served
    status, body, _ = breakdown(port, "metric=mem&group=web-app")
    top = json.loads(body)
    assert status == 200 and [x["pid"] for x in top["slices"]] == [900] and top["slices"][0]["has_children"] is True
    status, body, _ = breakdown(port, "metric=mem&group=web-app&parent=900")
    child = json.loads(body)
    assert status == 200 and {(x["pid"], x["kind"]) for x in child["slices"]} == {(901, "process"), (900, "self")}
    assert child["path"] == [{"pid": 900, "name": "vite"}]


def test_breakdown_rejects_gpu_unknown_groups_and_foreign_parents(served):
    port, _, _ = served
    assert breakdown(port, "metric=gpu&group=web-app")[0] == 400
    assert breakdown(port, "metric=mem&group=No%20Such")[0] == 400
    assert breakdown(port, "metric=mem&group=web-app&parent=950")[0] == 400
    assert breakdown(port, "metric=mem&group=web-app&parent=abc")[0] == 400
    assert breakdown(port, "metric=mem")[0] == 400


def test_breakdown_needs_the_token(served):
    port, _, _ = served
    assert call(port, "/api/breakdown?metric=mem&group=web-app")[0] == 403


# ------------------------------------------------------------------ preview (the basket's estimate)

def test_preview_lists_everything_that_would_be_signalled_and_the_saving_without_signalling(served):
    port, rec, _ = served
    status, body, _ = call(port, "/api/preview", "POST", {"pids": [900, 960]})
    result = json.loads(body)
    assert status == 200 and set(result["pids"]) == {900, 901}                  # children included
    assert {r["pid"] for r in result["refused"]} == {960} and result["refused"][0]["reason"]
    assert result["frees"]["mem"] > 0 and result["frees"]["cpu"] >= 0
    assert rec.calls == []


def test_preview_accepts_groups_and_validates_the_body(served):
    port, _, _ = served
    status, body, _ = call(port, "/api/preview", "POST", {"groups": ["web-app"]})
    assert status == 200 and set(json.loads(body)["pids"]) == {900, 901}
    assert call(port, "/api/preview", "POST", {"groups": ["No Such"]})[0] == 400
    assert call(port, "/api/preview", "POST", {})[0] == 400
    assert call(port, "/api/preview", "POST", {"pids": [900]}, token_header=False)[0] == 403


# ------------------------------------------------------------------ stop / force

def test_stop_sends_sigterm_to_a_process_and_its_children_children_first(served):
    port, rec, _ = served
    status, body, _ = call(port, "/api/stop", "POST", {"pids": [900]})
    result = json.loads(body)
    assert status == 200
    assert rec.calls == [(901, signal.SIGTERM), (900, signal.SIGTERM)]
    assert {(o["pid"], o["status"]) for o in result["outcomes"]} == {(901, "signalled"), (900, "signalled")}


def test_force_sends_sigkill(served):
    port, rec, _ = served
    status, _, _ = call(port, "/api/force", "POST", {"pids": [950]})
    assert status == 200 and rec.calls == [(950, signal.SIGKILL)]


def test_stopping_a_group_by_name_signals_every_member(served):
    port, rec, _ = served
    status, _, _ = call(port, "/api/stop", "POST", {"groups": ["web-app"]})
    assert status == 200 and {pid for pid, _ in rec.calls} == {900, 901}


def test_pids_and_groups_together_signal_each_process_once(served):
    port, rec, _ = served
    call(port, "/api/stop", "POST", {"pids": [900, 950], "groups": ["web-app"]})
    assert sorted(pid for pid, _ in rec.calls) == [900, 901, 950]


def test_refused_processes_are_reported_and_never_signalled(served):
    port, rec, _ = served
    status, body, _ = call(port, "/api/stop", "POST", {"pids": [1, 500, 400, 960, 950]})
    result = json.loads(body)
    assert status == 200 and rec.calls == [(950, signal.SIGTERM)]
    assert {r["pid"] for r in result["refused"]} == {1, 500, 400, 960}
    assert all(r["reason"] for r in result["refused"])


def test_an_unknown_group_or_bad_body_is_a_400_and_signals_nothing(served):
    port, rec, _ = served
    assert call(port, "/api/stop", "POST", {"groups": ["No Such Group"]})[0] == 400
    assert call(port, "/api/stop", "POST", {"groups": "web-app"})[0] == 400
    assert call(port, "/api/stop", "POST", {"nonsense": 1})[0] == 400
    assert call(port, "/api/stop", "POST", {})[0] == 400
    assert call(port, "/api/stop", "POST", {"pids": [], "groups": []})[0] == 400
    assert call(port, "/api/stop", "POST", {"pids": "900"})[0] == 400
    assert rec.calls == []


def test_posts_without_the_token_or_a_same_origin_origin_are_403_and_signal_nothing(served):
    port, rec, _ = served
    assert call(port, "/api/stop", "POST", {"pids": [950]}, token_header=False)[0] == 403
    assert call(port, "/api/stop", "POST", {"pids": [950]}, origin=False)[0] == 403
    assert call(port, "/api/stop", "POST", {"pids": [950]}, headers={"Origin": "http://evil.example.com"})[0] == 403
    assert call(port, "/api/stop", "POST", {"pids": [950]}, headers={"X-Procwatch-Token": "wrong"})[0] == 403
    assert rec.calls == []


def test_a_pid_whose_start_time_changed_since_the_snapshot_is_not_signalled():
    rec = Recorder()
    state = {"n": 0}

    def collect():
        # first read (planning) says pid 950 started at 3.0; by execution it is a different process
        state["n"] += 1
        return [p if p.pid != 950 or state["n"] == 1 else P(950, 1, "Other", "/x", create_time=99.0) for p in PROCS]

    server = create_server(collect_fn=collect, machine_fn=lambda: MACH, signal_fn=rec, token=TOKEN, projects_root=ROOT, me="tester", self_pid=500, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body, _ = call(port, "/api/stop", "POST", {"pids": [950]})
        assert status == 200 and rec.calls == []
        assert json.loads(body)["outcomes"][0]["status"] == "reused"
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------ read-only mode

def test_read_only_mode_serves_data_but_never_signals():
    rec = Recorder()
    server = create_server(collect_fn=lambda: list(PROCS), machine_fn=lambda: MACH, signal_fn=rec, token=TOKEN, projects_root=ROOT,
                           me="tester", self_pid=500, allow_kill=False, port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert snapshot(port)["snapshot"]["slices"]
        assert call(port, "/api/preview", "POST", {"pids": [950]})[0] == 200          # preview changes nothing, so it is allowed
        for path in ("/api/stop", "/api/force"):
            status, body, _ = call(port, path, "POST", {"pids": [950]})
            assert status == 403 and "read-only" in json.loads(body)["error"]
        assert rec.calls == []
    finally:
        server.shutdown()
        server.server_close()
