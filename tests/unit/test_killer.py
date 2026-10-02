import signal

from procwatch.killer import Outcome, StopPlan, Target, execute, plan

KW = dict(me="tester", self_pid=500, ancestors_of_self=frozenset({400}))


def pids(stop_plan):
    return [t.pid for t in stop_plan.order]


# ------------------------------------------------------------------ plan

def test_a_single_process_is_planned_with_its_start_time(proc):
    p = proc(900, create_time=42.5)
    result = plan([900], [p], **KW)
    assert result.order == (Target(900, 42.5),) and result.refused == ()


def test_children_are_included_and_signalled_before_their_parent(proc):
    procs = [proc(10), proc(11, ppid=10), proc(12, ppid=11), proc(13, ppid=10)]
    order = pids(plan([10], procs, **KW))
    assert set(order) == {10, 11, 12, 13}
    assert order.index(12) < order.index(11) < order.index(10)      # deepest first
    assert order.index(13) < order.index(10)


def test_a_group_selection_has_no_duplicates_when_members_are_related(proc):
    procs = [proc(10), proc(11, ppid=10)]
    order = pids(plan([10, 11], procs, **KW))
    assert sorted(order) == [10, 11] and len(order) == 2


def test_refused_processes_are_reported_with_a_reason_and_never_ordered(proc):
    procs = [proc(900), proc(901, username="root"), proc(500)]
    result = plan([900, 901, 500], procs, **KW)
    assert pids(result) == [900]
    assert {pid for pid, _ in result.refused} == {901, 500}
    assert all(reason for _, reason in result.refused)


def test_children_of_a_refused_process_are_not_pulled_in(proc):
    procs = [proc(20, name="Dock"), proc(21, ppid=20)]
    result = plan([20], procs, **KW)
    assert pids(result) == []
    assert [pid for pid, _ in result.refused] == [20]


def test_a_protected_child_of_an_allowed_process_is_refused_not_signalled(proc):
    procs = [proc(10), proc(11, ppid=10, name="Finder")]
    result = plan([10], procs, **KW)
    assert pids(result) == [10]
    assert [pid for pid, _ in result.refused] == [11]


def test_an_unknown_pid_is_refused_as_gone_not_an_error(proc):
    result = plan([12345], [proc(900)], **KW)
    assert pids(result) == [] and result.refused[0][0] == 12345


def test_planning_the_launching_terminal_is_refused_even_via_a_group(proc):
    procs = [proc(400, name="Terminal"), proc(900, ppid=400)]
    result = plan([400, 900], procs, **KW)
    assert 400 not in pids(result) and 400 in {pid for pid, _ in result.refused}


# --------------------------------------------------------------- execute

class Recorder:
    def __init__(self, raises=None):
        self.calls, self.raises = [], raises or {}

    def __call__(self, pid, sig):
        self.calls.append((pid, sig))
        if pid in self.raises:
            raise self.raises[pid]


def plan_of(*targets):
    return StopPlan(order=tuple(targets), refused=())


def test_execute_signals_each_target_in_order_and_reports_success():
    rec = Recorder()
    p = plan_of(Target(12, 1.0), Target(11, 2.0), Target(10, 3.0))
    outcomes = execute(p, signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: {12: 1.0, 11: 2.0, 10: 3.0}[pid])
    assert rec.calls == [(12, signal.SIGTERM), (11, signal.SIGTERM), (10, signal.SIGTERM)]
    assert outcomes == [Outcome(12, "signalled"), Outcome(11, "signalled"), Outcome(10, "signalled")]


def test_force_uses_the_signal_it_is_given():
    rec = Recorder()
    execute(plan_of(Target(10, 1.0)), signal.SIGKILL, signal_fn=rec, create_time_of=lambda pid: 1.0)
    assert rec.calls == [(10, signal.SIGKILL)]


def test_a_reused_pid_is_skipped_without_signalling():
    rec = Recorder()
    outcomes = execute(plan_of(Target(10, 1.0)), signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: 999.0)
    assert rec.calls == [] and outcomes == [Outcome(10, "reused")]


def test_a_process_that_already_exited_is_gone_not_an_error():
    rec = Recorder()
    outcomes = execute(plan_of(Target(10, 1.0)), signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: None)
    assert rec.calls == [] and outcomes == [Outcome(10, "gone")]


def test_a_process_that_vanishes_between_check_and_signal_is_gone():
    rec = Recorder(raises={10: ProcessLookupError()})
    outcomes = execute(plan_of(Target(10, 1.0)), signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: 1.0)
    assert outcomes == [Outcome(10, "gone")]


def test_permission_errors_and_other_oserrors_are_reported_and_do_not_stop_the_rest():
    rec = Recorder(raises={12: PermissionError(), 11: OSError("boom")})
    p = plan_of(Target(12, 1.0), Target(11, 1.0), Target(10, 1.0))
    outcomes = execute(p, signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: 1.0)
    assert [(o.pid, o.status) for o in outcomes] == [(12, "denied"), (11, "error"), (10, "signalled")]
    assert [c[0] for c in rec.calls] == [12, 11, 10]


def test_an_empty_plan_sends_nothing():
    rec = Recorder()
    assert execute(StopPlan(order=(), refused=()), signal.SIGTERM, signal_fn=rec, create_time_of=lambda pid: 1.0) == []
    assert rec.calls == []
