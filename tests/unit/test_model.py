from procwatch.model import ancestors


def test_ancestors_walks_up_the_parent_chain_nearest_first_set(proc):
    procs = [proc(1, ppid=0), proc(10, ppid=1), proc(20, ppid=10), proc(30, ppid=20)]
    assert ancestors(30, procs) == frozenset({20, 10, 1})


def test_ancestors_excludes_the_process_itself_and_stops_at_a_missing_parent(proc):
    procs = [proc(20, ppid=999), proc(30, ppid=20)]
    assert ancestors(30, procs) == frozenset({20})
    assert 30 not in ancestors(30, procs)


def test_ancestors_of_an_unknown_pid_is_empty(proc):
    assert ancestors(5, [proc(1, ppid=0)]) == frozenset()


def test_ancestors_survives_a_parent_cycle(proc):
    procs = [proc(10, ppid=20), proc(20, ppid=10)]
    assert ancestors(10, procs) == frozenset({20})
