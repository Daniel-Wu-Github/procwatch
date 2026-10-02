"""The pie is the whole machine: group slices are scaled to the OS's 'used'
figure and a Free/Idle slice fills the rest."""

import pytest

from procwatch.grouping import Label
from procwatch.snapshot import breakdown, build_snapshot, freed, origin

MB = 1024 * 1024


def snap(procs, labels, machine, metric="mem", **kw):
    return build_snapshot(procs, labels, machine, metric=metric, **kw)


def labelled(procs, name_of):
    return {p.pid: Label(name_of(p), "app") for p in procs}


# ------------------------------------------------------------ memory pie

def test_memory_pie_scales_groups_to_used_and_adds_a_free_slice(proc, machine):
    # raw RSS: A = 100+100, B = 600 (sum 800 MB) but the OS says only 400 MB is used -> scale 0.5
    procs = [proc(1, rss=100 * MB), proc(2, rss=600 * MB), proc(3, rss=100 * MB)]
    labels = {1: Label("A", "app"), 2: Label("B", "project"), 3: Label("A", "app")}
    s = snap(procs, labels, machine(mem_total=1000 * MB, mem_available=600 * MB))
    assert [(x["label"], x["value"], x["kind"]) for x in s["slices"]] == [
        ("B", 300 * MB, "project"), ("A", 100 * MB, "app"), ("Free", 600 * MB, "free")]
    assert s["total"] == 1000 * MB and s["used"] == 400 * MB and s["metric"] == "mem" and s["approx"] is True
    assert [x["count"] for x in s["slices"][:2]] == [1, 2]
    assert [round(x["share"], 2) for x in s["slices"]] == [30.0, 10.0, 60.0]


def test_group_details_keep_the_raw_unscaled_number_and_pids(proc, machine):
    procs = [proc(1, rss=100 * MB), proc(2, rss=100 * MB), proc(3, rss=600 * MB)]
    labels = {1: Label("A", "app"), 2: Label("A", "app"), 3: Label("B", "app")}
    s = snap(procs, labels, machine())
    assert s["groups"]["A"]["raw"] == 200 * MB and s["groups"]["A"]["value"] == 100 * MB
    assert sorted(s["groups"]["A"]["pids"]) == [1, 2] and s["groups"]["A"]["kind"] == "app"


def test_shares_always_sum_to_one_hundred(proc, machine):
    procs = [proc(i, rss=(i + 1) * MB) for i in range(1, 9)]
    s = snap(procs, labelled(procs, lambda p: f"g{p.pid}"), machine())
    assert sum(x["share"] for x in s["slices"]) == pytest.approx(100.0, abs=0.01)


def test_the_tail_beyond_top_n_merges_into_everything_else_before_free(proc, machine):
    procs = [proc(i, rss=(20 - i) * MB) for i in range(1, 7)]       # 19,18,17,16,15,14 MB
    s = snap(procs, labelled(procs, lambda p: f"g{p.pid}"), machine(), top_n=3)
    assert [x["label"] for x in s["slices"]] == ["g1", "g2", "g3", "Everything else", "Free"]
    other = s["slices"][3]
    assert other["kind"] == "other" and other["count"] == 3
    assert len(s["groups"]) == 6                                      # drill-down data keeps every group


def test_no_everything_else_slice_when_everything_fits(proc, machine):
    s = snap([proc(1, rss=MB)], {1: Label("A", "app")}, machine(), top_n=3)
    assert [x["label"] for x in s["slices"]] == ["A", "Free"]


def test_when_no_process_reports_memory_the_used_part_is_unattributed(proc, machine):
    s = snap([proc(1, rss=0)], {1: Label("A", "app")}, machine(mem_total=1000 * MB, mem_available=600 * MB))
    assert [(x["label"], x["kind"], x["value"]) for x in s["slices"]] == [
        ("Unattributed", "unattributed", 400 * MB), ("Free", "free", 600 * MB)]


def test_an_empty_process_list_is_all_unattributed_and_free(proc, machine):
    s = snap([], {}, machine())
    assert [x["label"] for x in s["slices"]] == ["Unattributed", "Free"] and s["groups"] == {}


def test_a_completely_idle_machine_is_just_a_free_slice(proc, machine):
    s = snap([proc(1, rss=0)], {1: Label("A", "app")}, machine(mem_total=1000 * MB, mem_available=1000 * MB))
    assert [(x["label"], x["share"]) for x in s["slices"]] == [("Free", 100.0)]


def test_available_above_total_is_clamped_not_negative(proc, machine):
    s = snap([proc(1, rss=MB)], {1: Label("A", "app")}, machine(mem_total=1000 * MB, mem_available=1500 * MB))
    assert s["used"] == 0 and all(x["value"] >= 0 for x in s["slices"])
    assert sum(x["share"] for x in s["slices"]) == pytest.approx(100.0, abs=0.01)


def test_a_zero_total_machine_does_not_divide_by_zero(proc, machine):
    s = snap([proc(1, rss=MB)], {1: Label("A", "app")}, machine(mem_total=0, mem_available=0))
    assert all(x["share"] == 0.0 for x in s["slices"])


# ------------------------------------------------------------ cpu / gpu

def test_cpu_pie_is_percent_of_the_whole_machine_with_an_idle_slice(proc, machine):
    # per-process cpu sums to 80, the machine reports 40% busy -> scale 0.5
    procs = [proc(1, rss=999 * MB, cpu=20.0), proc(2, rss=1, cpu=60.0)]
    s = snap(procs, {1: Label("A", "app"), 2: Label("B", "app")}, machine(cpu_busy_pct=40.0), metric="cpu")
    assert [(x["label"], x["value"], x["kind"]) for x in s["slices"]] == [("B", 30.0, "app"), ("A", 10.0, "app"), ("Idle", 60.0, "free")]
    assert s["metric"] == "cpu" and s["total"] == 100 and s["used"] == 40.0


def test_cpu_with_no_per_process_activity_is_unattributed(proc, machine):
    s = snap([proc(1, cpu=0.0)], {1: Label("A", "app")}, machine(cpu_busy_pct=25.0), metric="cpu")
    assert [(x["label"], x["value"]) for x in s["slices"]] == [("Unattributed", 25.0), ("Idle", 75.0)]


def test_gpu_is_machine_level_busy_vs_idle_with_a_note(proc, machine):
    s = snap([proc(1, rss=MB)], {1: Label("A", "app")}, machine(gpu_busy_pct=25.0), metric="gpu")
    assert [(x["label"], x["value"]) for x in s["slices"]] == [("GPU busy", 25.0), ("Idle", 75.0)]
    assert s["total"] == 100 and s["groups"] == {} and "per-app" in s["note"].lower()
    assert sum(x["share"] for x in s["slices"]) == pytest.approx(100.0, abs=0.01)


def test_gpu_unreadable_is_an_empty_pie_with_an_explanation(proc, machine):
    s = snap([proc(1)], {1: Label("A", "app")}, machine(gpu_busy_pct=None), metric="gpu")
    assert s["slices"] == [] and s["total"] == 0 and s["note"]


def test_unknown_metric_is_a_value_error(proc, machine):
    with pytest.raises(ValueError):
        snap([proc(1)], {1: Label("A", "app")}, machine(), metric="disk")


# ------------------------------------------------------------ breakdown (drill-down)

def forest(proc):
    """Group A: 10 -> (11 -> 13), 12 ; plus root 20. Group B: 30. Raw RSS 1000 MB total."""
    procs = [
        proc(10, ppid=1, name="root-a", rss=100 * MB, cpu=10.0),
        proc(11, ppid=10, name="kid-a", rss=50 * MB, cpu=5.0),
        proc(12, ppid=10, name="kid-b", rss=50 * MB, cpu=5.0),
        proc(13, ppid=11, name="grandkid", rss=100 * MB, cpu=10.0),
        proc(20, ppid=1, name="other-root", rss=200 * MB, cpu=20.0),
        proc(30, ppid=1, name="b-root", rss=500 * MB, cpu=50.0),
    ]
    labels = {pid: Label("A", "app") for pid in (10, 11, 12, 13, 20)}
    labels[30] = Label("B", "app")
    return procs, labels


def test_breakdown_of_a_group_lists_its_roots_with_subtree_totals(proc, machine):
    procs, labels = forest(proc)
    b = breakdown(procs, labels, machine(mem_total=1000 * MB, mem_available=500 * MB), metric="mem", group="A")
    # scale = used(500) / raw(1000) = 0.5; group A raw 500 -> 250
    assert b["total"] == 250 * MB and b["group"] == "A" and b["parent_pid"] is None and b["approx"] is True
    assert [(x["pid"], x["value"], x["count"], x["has_children"], x["kind"]) for x in b["slices"]] == [
        (10, 150 * MB, 4, True, "process"), (20, 100 * MB, 1, False, "process")]
    assert [round(x["share"], 1) for x in b["slices"]] == [60.0, 40.0]
    assert b["path"] == []


def test_breakdown_of_a_process_shows_its_children_and_its_own_usage(proc, machine):
    procs, labels = forest(proc)
    m = machine(mem_total=1000 * MB, mem_available=500 * MB)
    b = breakdown(procs, labels, m, metric="mem", group="A", parent_pid=10)
    assert b["total"] == 150 * MB                                           # the subtree of pid 10
    assert [(x["pid"], x["kind"], x["value"]) for x in b["slices"]] == [
        (11, "process", 75 * MB), (10, "self", 50 * MB), (12, "process", 25 * MB)]
    assert b["slices"][1]["label"] == "root-a"
    assert b["path"] == [{"pid": 10, "name": "root-a"}]
    assert sum(x["value"] for x in b["slices"]) == b["total"]


def test_breakdown_path_is_the_chain_from_the_group_root(proc, machine):
    procs, labels = forest(proc)
    b = breakdown(procs, labels, machine(), metric="mem", group="A", parent_pid=11)
    assert b["path"] == [{"pid": 10, "name": "root-a"}, {"pid": 11, "name": "kid-a"}]
    assert [(x["pid"], x["kind"]) for x in b["slices"]] == [(13, "process"), (11, "self")]


def test_a_child_that_belongs_to_another_group_is_not_in_this_breakdown(proc, machine):
    procs, labels = forest(proc)
    procs.append(proc(14, ppid=10, name="foreign", rss=300 * MB))
    labels[14] = Label("B", "app")
    b = breakdown(procs, labels, machine(), metric="mem", group="A", parent_pid=10)
    assert 14 not in [x["pid"] for x in b["slices"]]
    roots_b = breakdown(procs, labels, machine(), metric="mem", group="B")
    assert {x["pid"] for x in roots_b["slices"]} == {30, 14}              # its parent is outside the group, so it is a root


def test_breakdown_merges_the_tail_into_everything_else(proc, machine):
    procs = [proc(i, ppid=1, name=f"r{i}", rss=(10 - i) * MB) for i in range(2, 7)]
    labels = {p.pid: Label("A", "app") for p in procs}
    b = breakdown(procs, labels, machine(), metric="mem", group="A", top_n=2)
    assert [x["kind"] for x in b["slices"]] == ["process", "process", "other"]
    assert b["slices"][-1]["label"] == "Everything else" and b["slices"][-1]["count"] == 3
    assert b["slices"][-1]["has_children"] is False


def test_cpu_breakdown_uses_the_cpu_scale(proc, machine):
    procs, labels = forest(proc)
    # raw cpu sums to 100; machine busy 50 -> scale 0.5; group A raw 50 -> 25
    b = breakdown(procs, labels, machine(cpu_busy_pct=50.0), metric="cpu", group="A")
    assert b["total"] == pytest.approx(25.0)
    assert [x["pid"] for x in b["slices"]] == [10, 20]


@pytest.mark.parametrize("kwargs", [
    dict(metric="gpu", group="A"),                 # no per-app GPU
    dict(metric="mem", group="Nope"),
    dict(metric="mem", group="A", parent_pid=30),  # pid 30 is not in group A
    dict(metric="mem", group="A", parent_pid=999),
    dict(metric="disk", group="A"),
])
def test_breakdown_rejects_bad_requests(proc, machine, kwargs):
    procs, labels = forest(proc)
    with pytest.raises(ValueError):
        breakdown(procs, labels, machine(), **kwargs)


# ------------------------------------------------------------ freed (the basket's estimate)

def test_freed_is_scaled_like_the_pie(proc, machine):
    procs, _ = forest(proc)                          # raw rss 1000 MB, raw cpu 100
    m = machine(mem_total=1000 * MB, mem_available=500 * MB, cpu_busy_pct=50.0)
    result = freed([10, 20], procs, m)               # raw 300 MB / 30 cpu -> x0.5
    assert result["mem"] == 150 * MB and isinstance(result["mem"], int)
    assert result["cpu"] == pytest.approx(15.0)


def test_freed_ignores_unknown_pids_and_handles_empty_input(proc, machine):
    procs, _ = forest(proc)
    assert freed([999], procs, machine()) == {"mem": 0, "cpu": 0.0}
    assert freed([], procs, machine()) == {"mem": 0, "cpu": 0.0}
    assert freed([1], [], machine()) == {"mem": 0, "cpu": 0.0}              # no processes at all: no ZeroDivisionError


def test_freed_counts_a_pid_once_even_if_listed_twice(proc, machine):
    procs, _ = forest(proc)
    m = machine(mem_total=1000 * MB, mem_available=500 * MB)
    assert freed([10, 10], procs, m) == freed([10], procs, m)


# ------------------------------------------------------------ origin

def test_origin_reports_path_cwd_command_and_parent_chain_nearest_first(proc):
    procs = [
        proc(1, ppid=0, name="launchd"),
        proc(10, ppid=1, name="zsh"),
        proc(20, ppid=10, name="npm"),
        proc(30, ppid=20, name="vite", exe="/opt/homebrew/bin/node", cwd="/Users/tester/projects/web-app/web",
             cmdline=("node", "vite", "--port", "5173")),
    ]
    o = origin(procs[-1], procs)
    assert o["exe"] == "/opt/homebrew/bin/node" and o["cwd"] == "/Users/tester/projects/web-app/web"
    assert o["cmdline"] == "node vite --port 5173"
    assert o["parents"] == [{"pid": 20, "name": "npm"}, {"pid": 10, "name": "zsh"}, {"pid": 1, "name": "launchd"}]


def test_origin_keeps_missing_data_as_none_and_empty_cmdline_as_none(proc):
    p = proc(30, ppid=999, exe=None, cwd=None, cmdline=())
    o = origin(p, [p])
    assert o["exe"] is None and o["cwd"] is None and o["cmdline"] is None and o["parents"] == []


def test_origin_survives_a_parent_cycle(proc):
    a, b = proc(10, ppid=20, name="a"), proc(20, ppid=10, name="b")
    assert origin(a, [a, b])["parents"] == [{"pid": 20, "name": "b"}]
