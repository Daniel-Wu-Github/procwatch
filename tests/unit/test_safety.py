import pytest

from procwatch.safety import check

KW = dict(me="tester", self_pid=500, ancestors_of_self=frozenset({400, 300}))


def test_an_ordinary_process_you_own_is_allowed(proc):
    verdict = check(proc(900, name="vite"), **KW)
    assert verdict.allowed is True


def test_a_process_owned_by_someone_else_is_refused_with_a_reason(proc):
    verdict = check(proc(900, username="root"), **KW)
    assert verdict.allowed is False and "own" in verdict.reason.lower()


def test_unknown_owner_is_refused(proc):
    assert check(proc(900, username=None), **KW).allowed is False


@pytest.mark.parametrize("pid", [0, 1])
def test_pid_0_and_1_are_never_signalled(proc, pid):
    assert check(proc(pid), **KW).allowed is False


def test_the_tool_itself_is_refused(proc):
    verdict = check(proc(500), **KW)
    assert verdict.allowed is False and "procwatch" in verdict.reason.lower()


def test_your_terminal_and_shell_that_launched_the_tool_are_refused(proc):
    for pid in (400, 300):
        verdict = check(proc(pid), **KW)
        assert verdict.allowed is False and "launched" in verdict.reason.lower()


@pytest.mark.parametrize("name", ["launchd", "kernel_task", "WindowServer", "loginwindow", "Finder", "Dock", "SystemUIServer", "coreaudiod"])
def test_protected_system_processes_are_refused_even_if_you_own_them(proc, name):
    verdict = check(proc(900, name=name), **KW)
    assert verdict.allowed is False and "protected" in verdict.reason.lower()


def test_every_refusal_has_a_human_readable_reason(proc):
    for p in (proc(900, username="root"), proc(1), proc(500), proc(900, name="Dock")):
        verdict = check(p, **KW)
        assert verdict.allowed is False and verdict.reason.strip()
