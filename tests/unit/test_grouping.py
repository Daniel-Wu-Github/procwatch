from procwatch.grouping import Label, Rule, classify

ROOT = "/Users/tester/projects"


def labels(procs, rules=()):
    return classify(procs, list(rules), ROOT)


def test_app_bundle_name_comes_from_the_exe_path(proc):
    p = proc(10, exe="/Applications/Vivaldi.app/Contents/MacOS/Vivaldi")
    assert labels([p])[10] == Label("Vivaldi", "app")


def test_nested_helper_app_uses_the_outermost_bundle(proc):
    exe = "/Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper (Renderer)"
    assert labels([proc(10, exe=exe)])[10] == Label("Claude", "app")


def test_project_comes_from_the_working_directory(proc):
    p = proc(10, exe="/opt/homebrew/bin/node", cwd=f"{ROOT}/web-app/web")
    assert labels([p])[10] == Label("web-app", "project")


def test_project_can_come_from_the_command_line_when_cwd_is_unreadable(proc):
    p = proc(10, exe="/opt/homebrew/bin/node", cwd=None,
             cmdline=("node", f"{ROOT}/web-app/web/node_modules/.bin/vite", "--port", "1"))
    assert labels([p])[10] == Label("web-app", "project")


def test_project_beats_app_so_a_homebrew_python_in_a_project_is_the_project(proc):
    exe = "/opt/homebrew/Cellar/python@3.12/3.12.13_4/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
    p = proc(10, exe=exe, cwd=f"{ROOT}/my-app/backend")
    assert labels([p])[10] == Label("my-app", "project")


def test_a_path_that_merely_shares_the_prefix_is_not_a_project(proc):
    p = proc(10, exe="/opt/homebrew/bin/node", cwd=f"{ROOT}-archive/old")
    assert labels([p])[10].kind != "project"


def test_system_paths_are_macos_system(proc):
    for exe in ("/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", "/usr/sbin/cfprefsd", "/sbin/launchd"):
        assert labels([proc(10, exe=exe)])[10] == Label("macOS system", "system")


def test_rules_beat_every_auto_detection(proc):
    p = proc(10, exe="/Applications/CrossOver.app/Contents/MacOS/CrossOver", cwd=f"{ROOT}/foo")
    rule = Rule(label="Games", exe_contains="crossover")           # case-insensitive substring
    assert labels([p], [rule])[10] == Label("Games", "rule")


def test_first_matching_rule_wins(proc):
    p = proc(10, name="node", exe="/opt/homebrew/bin/node")
    rules = [Rule(label="First", name="NODE"), Rule(label="Second", name="node")]
    assert labels([p], rules)[10].name == "First"


def test_rule_conditions_are_anded_and_a_none_field_never_matches(proc):
    rule = Rule(label="R", exe_contains="node", cwd_prefix="/tmp")
    assert labels([proc(10, exe="/bin/node", cwd="/tmp/x")], [rule])[10].name == "R"
    assert labels([proc(11, exe="/bin/node", cwd="/var")], [rule])[11].name != "R"
    assert labels([proc(12, exe="/bin/node", cwd=None)], [rule])[12].name != "R"
    assert labels([proc(13, exe=None, cwd="/tmp/x")], [rule])[13].name != "R"


def test_rule_prefixes_are_exact_and_cmdline_match_is_case_insensitive(proc):
    assert labels([proc(10, exe="/Apps/x")], [Rule(label="R", exe_prefix="/apps")])[10].name != "R"
    assert labels([proc(11, cmdline=("python", "-m", "App.Visual.Trailers"))], [Rule(label="R", cmdline_contains="app.visual.")])[11].name == "R"


def test_a_child_inherits_the_label_of_an_app_or_project_parent(proc):
    parent = proc(10, exe="/Applications/Docker.app/Contents/MacOS/com.docker.backend")
    child = proc(11, ppid=10, name="helper", exe=None)
    grandchild = proc(12, ppid=11, name="worker", exe="/opt/homebrew/bin/worker")
    result = labels([parent, child, grandchild])
    assert result[11] == Label("Docker", "inherited")
    assert result[12] == Label("Docker", "inherited")


def test_a_child_does_not_inherit_from_a_system_or_other_parent(proc):
    launchd = proc(1, ppid=0, exe="/sbin/launchd")
    stray = proc(20, ppid=1, name="mystery", exe="/opt/homebrew/bin/mystery")
    result = labels([launchd, stray])
    assert result[20] == Label("Other: mystery", "other")


def test_unknown_process_without_exe_falls_back_to_its_name(proc):
    assert labels([proc(10, name="weird", exe=None)])[10] == Label("Other: weird", "other")


def test_a_parent_cycle_does_not_hang_and_still_labels_everyone(proc):
    a, b = proc(10, ppid=20, name="a"), proc(20, ppid=10, name="b")
    result = labels([a, b])
    assert set(result) == {10, 20}


def test_every_process_gets_a_label(proc):
    procs = [proc(i, ppid=max(i - 1, 1), name=f"p{i}") for i in range(1, 30)]
    assert set(labels(procs)) == {p.pid for p in procs}
