# procwatch — plan

A tiny local tool to free up a personal machine (e.g. before gaming): a pie
chart of where memory/CPU goes, per-process and per-group Stop/Force, and
for every process where it came from (which app or project folder, and the
exact path).

Status: **v1.0 released; implemented, automated verification and read-only checks passed.**
Platform: **macOS only** (see Known limits).
Disposable-process shutdown and Playwright operational checks also passed.
The venv command (`.venv/bin/procs`) is usable directly. Joint grouping-rule review remains optional.

## Decisions (made 2026-09-30)

| Question | Chosen | Why / trade-off accepted |
|---|---|---|
| Interface | **Local web page** (stdlib `http.server` + one HTML page, inline SVG pie, no external libs) | Real pie + clickable slices, easiest to change, no install beyond Python. Cost: a local server runs while open, so it needs token + Host/Origin checks (below). |
| Memory metric | **RSS via psutil** | Instant and works for all your own processes. Cost: double-counts memory shared between processes (browsers), so slices rank correctly but won't match Activity Monitor to the MB. The page must say so. |
| Stop behaviour | **Two-step: Stop (SIGTERM), then a separate Force (SIGKILL)** | Safest for databases/Docker; you choose when to force. Cost: more clicks. No automatic escalation anywhere. |
| Grouping | **Auto-detect + editable rules file** | Works out of the box, fixable when it guesses wrong. Cost: one more file; a rule typo can mislabel (invalid rules must fail loudly, see below). |

| Pie total | **Pie = the whole machine.** Memory slices are scaled to the OS's "used" figure; the remainder is a **Free** slice | Always adds up and Free matches Activity Monitor. Cost: group sizes are proportional estimates (RSS double-counts shared memory); the page says "approx". |
| GPU | **Machine-level only** (no sudo): busy vs idle, no per-app breakdown | macOS only exposes per-process GPU with admin rights; a root helper is too risky for a tool that kills processes. |
| Click a slice | **The pie is replaced by that slice's breakdown, with breadcrumbs**, drilling group → processes → child processes | Clean, uses the whole chart at each level. Cost: one level visible at a time. |
| Stopping flow | **Checkboxes + a "Free up" basket** showing the estimated saving, one confirm | Matches the goal (free resources in one go). Cost: one more concept than per-item buttons. |
| Theme | **Light only** | One palette to get right. Cost: bright at night. |
| Live updates | **Animated, paused while you interact** (mouse on a slice, dialog open) with a live/paused indicator | Feels alive but never moves under the cursor. Cost: pause logic. |
| Layout | **Pie left, details panel right** (stacks under 900 px) | Standard dashboard pattern. |
| Protected processes | **Shown dimmed with a lock and the reason** | The pie and its sub-lists stay consistent. |
| Explain model | **Optional, user-configured OpenAI-compatible endpoint** (`config.toml` `[explain]`, flags override). Off until configured; loopback by default; a non-loopback endpoint needs `allow_remote` and https. (Decided 2026-10-02; supersedes the hardcoded `127.0.0.1:8011` shared service.) | No bundled weights or licence burden; any local or hosted model works. Cost: one more config file; remote endpoints can leave the machine, so they are opt-in, flagged in the UI, and never receive command lines or usernames. The shipped `local_model/` service stays as an optional, separate example. |
| Instances | **One dashboard per user, enforced by an OS file lock** (`instance.py`, `flock` on `~/Library/Caches/procwatch/instance.lock`, override with `PROCWATCH_INSTANCE_DIR`) | A repeat `procs` reuses the running URL instead of starting a second server that could signal the same processes. The lock dies with the process, so a crash never leaves a stale lease; the lock file is never unlinked. Cost: a repeat launch cannot change mode or options. |
| Process explanations | **Advisory generation from bounded process facts** | No command arguments or usernames sent; generated text never authorizes or performs signals. A custom prompt replaces only the instruction text; a fixed safety guard is always appended. At most 2 explanations in flight (HTTP 429 beyond that). |

Defaults not asked: Python 3.12 + `psutil`; bind `127.0.0.1` only, random free
port, random per-run token; refresh every 2 s; confirm before any group stop.

## Architecture (package `procwatch/`)

Pure logic is separated from the OS so it is fully unit-testable:

| Module | Role | Tested by |
|---|---|---|
| `model.py` | `Proc` dataclass, `ancestors()` | `tests/unit/test_model.py` |
| `grouping.py` | rules parsing + `classify()` (who owns each process) | `test_rules.py`, `test_grouping.py` |
| `safety.py` | `check()`: may this process be signalled at all? | `test_safety.py` |
| `killer.py` | `plan()` (ordering, refusals) + `execute()` (signals, pid-reuse guard) | `test_killer.py` |
| `security.py` | `request_allowed()`: token, Host, Origin checks | `test_security.py` |
| `snapshot.py` | `build_snapshot()` (pie data), `origin()` (path/parent chain) | `test_snapshot.py` |
| `collector.py` | `collect()` reads processes via psutil | `tests/integration/test_collector.py` |
| `server.py` | `create_server()` HTTP API + HTML page (per-response CSP nonce, header or query token) | `tests/integration/test_server.py`, `test_hardening.py` |
| `config.py` | `load_explain_config()`: `[explain]` table, flags, URL/prompt/key validation | `test_config.py`, `test_cli_config.py`, `tests/integration/test_explain_config.py` |
| `llm.py` | `explain_processes(facts, config)`: redacted facts, prompt + guard, no redirects/proxies for loopback | `test_llm.py`, `test_explain.py` |
| `instance.py` | `Instance`: process-lifetime single-instance lease and published URL/mode | `test_instance.py`, `test_cli_instance.py` |
| `cli.py` | `procs` entry point: acquire lease, pick port, open browser, Ctrl-C to quit | `test_cli_instance.py`, manual |

### Contracts (the tests are the spec; these are the signatures)

```python
# model.py
@dataclass(frozen=True)
class Proc:
    pid: int; ppid: int; name: str
    exe: str | None; cwd: str | None; cmdline: tuple[str, ...]
    username: str | None; rss: int          # bytes
    cpu: float                              # percent
    create_time: float
def ancestors(pid: int, procs: Sequence[Proc]) -> frozenset[int]   # parents up the chain, excluding pid itself; cycle-safe
@dataclass(frozen=True)
class MachineStats:
    mem_total: int; mem_available: int      # bytes
    cpu_busy_pct: float                     # whole machine, 0-100
    cpu_cores: int
    gpu_busy_pct: float | None              # None when unreadable

# grouping.py
class RulesError(ValueError)
@dataclass(frozen=True)
class Rule: label: str; name=None; exe_prefix=None; exe_contains=None; cwd_prefix=None; cmdline_contains=None
@dataclass(frozen=True)
class Label: name: str; kind: str           # kind: rule | project | app | system | inherited | other
def parse_rules(text: str) -> list[Rule]    # TOML, see "Rules file"
def classify(procs: Sequence[Proc], rules: Sequence[Rule], projects_root: str) -> dict[int, Label]

# safety.py
@dataclass(frozen=True)
class Verdict: allowed: bool; reason: str
def check(proc: Proc, *, me: str, self_pid: int, ancestors_of_self: frozenset[int]) -> Verdict

# killer.py
@dataclass(frozen=True) class Target: pid: int; create_time: float
@dataclass(frozen=True) class StopPlan: order: tuple[Target, ...]; refused: tuple[tuple[int, str], ...]
@dataclass(frozen=True) class Outcome: pid: int; status: str   # signalled | gone | reused | denied | error
def plan(selection: Iterable[int], procs: Sequence[Proc], *, me: str, self_pid: int, ancestors_of_self: frozenset[int]) -> StopPlan
def execute(stop_plan: StopPlan, sig: int, *, signal_fn: Callable[[int, int], None], create_time_of: Callable[[int], float | None]) -> list[Outcome]

# security.py
def request_allowed(method: str, headers: Mapping[str, str], token: str | None, *, expected_token: str, port: int) -> bool

# snapshot.py
def build_snapshot(procs, labels: dict[int, Label], machine: MachineStats, *, metric: str, top_n: int = 10) -> dict
def breakdown(procs, labels, machine, *, metric: str, group: str, parent_pid: int | None = None, top_n: int = 10) -> dict
def freed(pids: Iterable[int], procs, machine) -> dict          # {"mem": bytes, "cpu": percent of whole machine}, scaled like the pie
def origin(proc: Proc, procs: Sequence[Proc]) -> dict

# collector.py
def collect() -> list[Proc]
def machine() -> MachineStats        # psutil for memory/CPU; GPU from `ioreg -r -d 1 -w 0 -c IOAccelerator`
                                     # ("Device Utilization %"), None if absent or unparsable. No sudo, ever.

# server.py
def create_server(*, collect_fn, machine_fn, signal_fn, token: str, projects_root: str, rules=(), me: str,
                  self_pid: int, allow_kill: bool = True, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer
```

### Grouping rules (auto-detect order; first hit wins)

1. A **rule** from the rules file matches → `Label(rule.label, "rule")`.
2. **Project**: `cwd`, else any `cmdline` element, is under `projects_root` →
   `Label(<first folder under projects_root>, "project")`. (Beats app, so a
   Homebrew `python`/`node` running in `~/projects/foo` is "foo".)
3. **System**: `exe` starts with `/System/`, `/usr/`, `/bin/`, `/sbin/`,
   `/Library/Apple/`, `/private/var/` → `Label("macOS system", "system")`.
   This precedes app detection, as required by `test_system_paths_are_macos_system`
   (including Finder under `/System/`); the original plan had these steps reversed.
4. **App**: `exe` contains a `.app/` bundle → the **outermost** bundle's name
   without `.app` (`Claude.app/.../Claude Helper (Renderer).app/...` → "Claude").
5. **Inherit** the parent's label (kind `inherited`, same name) when the
   parent's own kind is rule/project/app/inherited. Never inherit from
   system/other parents.
6. Else `Label("Other: " + basename(exe) or name, "other")`.

A process with `exe=None` (permission denied) skips rules needing exe and can
still be classified by rule 2 or 5. Parent links are cycle-safe.

### Rules file

`~/.config/procwatch/rules.toml` (override with `--rules PATH`; missing file =
no rules). Conditions in one rule are ANDed; a condition on a field that is
`None` does not match; string matches are case-insensitive except prefixes,
which are exact.

```toml
[[rule]]
label = "Games"
exe_contains = "CrossOver"

[[rule]]
label = "Crawlers"
cmdline_contains = "app.visual."
cwd_prefix = "/Users/me/projects/my-crawler"
```

Keys: `label` (required) plus at least one of `name` (exact, case-insensitive),
`exe_prefix`, `exe_contains`, `cwd_prefix`, `cmdline_contains`. Unknown keys,
a missing `label`, or no condition → `RulesError` (never silently ignored).

### Safety (must hold; all tested)

- Never offer/perform a signal on: a process not owned by `me`; pid 0/1;
  procwatch itself; any ancestor of procwatch (your terminal/shell); a
  protected name (`launchd`, `kernel_task`, `WindowServer`, `loginwindow`,
  `Finder`, `Dock`, `SystemUIServer`, `coreaudiod`) or an executable under
  `/usr/libexec/` or `/System/Library/` (added 2026-10-02; Apple apps in
  `/System/Applications/` stay stoppable).
- Stopping a group signals the **whole tree children-first**; children of
  an allowed target are included, children of a *refused* process are not.
- **PID-reuse guard:** `execute` re-reads `create_time` and skips a pid whose
  start time changed (`reused`) — a stale click must never hit a new process.
- Signals are only SIGTERM (Stop) and SIGKILL (Force). No auto-escalation.
  `ProcessLookupError` → `gone`, `PermissionError` → `denied`, other
  `OSError` → `error`; one failure never aborts the rest.
- Server: bound to `127.0.0.1`; every request needs the per-run token
  (constant-time compare) **and** a `Host` of `127.0.0.1:<port>` or
  `localhost:<port>` (DNS-rebinding guard); POSTs also need an `Origin`
  of `http://127.0.0.1:<port>` or `http://localhost:<port>` (CSRF guard).
- `--read-only` (maps to `allow_kill=False`): POST stop/force return 403,
  no signal is ever sent. The first run on a real machine uses this.

### How a stop request runs (the tests depend on this order)

1. Call `collect_fn()` once to get the table; resolve `groups` names with
   `classify` over that table; compute `ancestors(self_pid, procs)`.
2. `killer.plan(...)` on that table (this is the **first** `collect_fn` call).
3. `killer.execute(plan, SIGTERM|SIGKILL, signal_fn=..., create_time_of=...)`,
   where `create_time_of(pid)` does a **fresh** `collect_fn()` lookup
   (second call) and returns that pid's `create_time`, or `None` if absent.
   That fresh read is the pid-reuse guard; do not reuse the planning table.

### HTTP API (`server.py`)

- `GET /?token=T` → the HTML page (embeds the token for its fetches).
- `GET /api/snapshot?metric=mem|cpu|gpu&token=T` →
  `{"snapshot": build_snapshot(...), "machine": {mem_total, mem_available, cpu_busy_pct, cpu_cores, gpu_busy_pct},
  "procs": [{pid, ppid, name, group, kind, rss, cpu, origin, protected, reason}]}`.
- `GET /api/breakdown?metric=mem|cpu&group=NAME[&parent=PID]&token=T` → `breakdown(...)`.
  `gpu` → 400 (no per-app data); unknown group or a parent that is not in the group → 400.
- `POST /api/preview` (allowed in read-only mode: it changes nothing), body as for stop →
  `{"pids":[…everything that would be signalled, children included…], "refused":[{pid,reason}], "frees":{"mem":bytes,"cpu":pct}}`.
- `POST /api/stop` / `POST /api/force`, JSON body `{"pids":[…], "groups":["Name",…]}` (either or both,
  at least one non-empty), header `X-Procwatch-Token`. Returns
  `{"outcomes":[{pid,status}], "refused":[{pid,reason}]}`.
- `POST /api/explain` (same body, up to 12 processes) → `{"explanation", "pids", "advisory": true}`;
  503 when Explain is not configured or the endpoint is unavailable; 429 when two are already in flight.
- GET requests may carry the token as `?token=` or the `X-Procwatch-Token` header (the page uses the
  header and removes the token from its address bar after load).
- 403 for a bad token/Host/Origin (and for stop/force in read-only mode), 404 for
  unknown paths, 400 for a malformed body, an unknown group or an unknown metric.

### Snapshot shape (the pie is the whole machine)

`build_snapshot(procs, labels, machine, metric=..., top_n=10)` returns
`{"metric", "total", "used", "approx", "note", "slices":[{label, kind, value, share, count}], "groups":{label:{kind, value, raw, pids}}}`.

- **mem**: `total = mem_total`; `used = mem_total - mem_available`. Each group's
  `raw` is the sum of its `rss`; its `value` is `raw * used / sum(raw)`, so the
  groups together equal `used` (`approx = True`). A final **Free** slice
  (`kind "free"`, label `"Free"`, value `mem_available`) fills the rest. If every
  `rss` is 0 but `used > 0`, one `"Unattributed"` slice (kind `unattributed`)
  carries `used`.
- **cpu**: `total = 100` (percent of the whole machine); `used = cpu_busy_pct`;
  group `raw` = sum of per-process `cpu`; `value = raw * used / sum(raw)`;
  **Idle** slice (`kind "free"`, label `"Idle"`, value `100 - used`). Same
  `Unattributed` fallback.
- **gpu**: machine-level only. If `gpu_busy_pct` is not `None`: slices
  `"GPU busy"` (kind `gpu`) and `"Idle"` (kind `free`), `total = 100`,
  `groups = {}`, `note` explains per-app GPU is unavailable on macOS without
  administrator rights. If `None`: `slices = []`, `total = 0`, a `note` saying
  the GPU reading is unavailable.
- Clamp so `0 <= free <= total` and `used <= total`. Groups whose scaled `value`
  is 0 are left out of `slices` (they stay in `groups`).
- Slices: top `top_n` groups by `value`, descending; the rest merge into one
  `"Everything else"` (kind `other`); then the Free/Idle slice last.
  `share` is a percent **of `total`** and all shares sum to 100 (±0.01); with
  `total == 0` all shares are `0.0`. `groups` keeps every group unmerged
  (including `raw`, the unscaled number).
- `metric` anything else → `ValueError`.

`breakdown(...)`: the drill-down. `group` is a label; `parent_pid=None` lists
the group's **roots** (members whose parent is not in the group); a `parent_pid`
lists that process's direct children **within the group**, plus a `"self"` slice
(kind `self`, label = the process name) for the process's own usage, so the
slices add up to that process's subtree. Each slice is
`{label, pid, kind: "process"|"self"|"other", value, share, count, has_children}`
where `value` is the subtree total scaled by the same factor as `snapshot`,
`count` is the number of processes in the subtree and `share` is a percent of
the returned `total` (the clicked slice's value). Also returns `metric`,
`group`, `parent_pid`, `total`, `approx`, and `path` (the breadcrumb: the
process chain from the group root down to `parent_pid`, `[{pid, name}]`).
More than `top_n` items merge into `"Everything else"` (kind `other`, not
drillable). `metric="gpu"`, an unknown group, or a `parent_pid` outside the
group → `ValueError`.

`freed(pids, procs, machine)`: the saving shown in the basket, scaled like the
pie: `{"mem": sum(rss) * used/sum_all_rss (bytes, int), "cpu": sum(cpu) * busy/sum_all_cpu (percent)}`.
Unknown pids count as 0; no processes at all gives zeros.

`origin(proc, procs)` returns `{"exe", "cwd", "cmdline" (joined string or None if empty), "parents":[{pid, name}…]}`,
nearest parent first, stopping at pid 1 or a missing/looping parent. Missing
data stays `None` (the page shows "n/a").

## UI and UX standards (one page, light theme only)

**Look and feel (modern, sleek):**
- System font stack (`-apple-system, "SF Pro Text", system-ui, sans-serif`), an 8 px
  spacing grid, 12-16 px rounded cards on a soft grey page background with
  hairline borders and one subtle shadow level. Tabular numerals for all
  figures so values don't jitter. All colours are CSS variables defined once
  (page `#f5f6f8`, surface `#ffffff`, text `#1d1d1f`, secondary text `#5f6368`,
  border `#e5e7eb`, accent `#0a64d8`, danger `#c4161c`); text contrast
  meets WCAG AA (4.5:1). A single restrained accent; danger red only on
  Stop/Force and confirm buttons.
- Pie palette: 10 distinct, accessible hues for groups; **Free/Idle** is a
  neutral light grey with a dashed outline; **Unattributed** is hatched grey;
  **Everything else** is mid grey. Never rely on colour alone: every slice
  has a legend row with its name, value and share.
- Rendered as a **donut-style pie** (inline SVG) with the machine total in the
  centre: "24.8 of 48 GB used" (memory), "37% busy" (CPU/GPU), plus a
  small "approx" tag for memory.

**Pie interaction:**
- **Hover** a slice: it scales up (about 1.06, from the pie centre, 150 ms ease),
  the other slices dim to ~60 % opacity, the matching legend row highlights, and
  a tooltip follows the cursor with name, value, share and process count.
- **Click** a slice: the pie animates (about 300 ms) into the breakdown of that slice
  (group → its processes → a process's children), the **breadcrumb** above the
  pie shows the path (`Machine › Vivaldi › Vivaldi Helper`), and clicking any crumb,
  pressing **Esc** or **Backspace**, or a back arrow goes up one level. A slice
  with no children (`has_children` false) does not drill; clicking it just selects
  it. Free/Idle/Unattributed/Everything else do not drill.
- A drilled pie always totals the slice it came from, so the parts visibly add up.
- **Metric toggle**: a segmented control `Memory | CPU | GPU` at the top of the
  pie card, also keys **1 / 2 / 3**. Switching resets to the machine level.
  GPU shows the busy/idle pie with the note that per-app GPU isn't available,
  and no drill-down.
- Respect `prefers-reduced-motion` (no scaling/morph animation, instant swaps).

**Layout:** pie card on the left (about 45 %), details panel on the right; below
900 px they stack. Sticky header: title, metric toggle, the live/paused
indicator, and the read-only badge when `--read-only`.

**Details panel** (for the drilled slice): a table of its processes with name,
pid, memory/CPU, a checkbox, and a one-line origin ("Vivaldi.app", or the
project folder). Selecting a row expands it to the full **exe path, cwd,
command line and parent chain** with a **Copy path** button. Protected or
not-yours processes are **dimmed with a lock icon and a tooltip giving the
reason**, no checkbox, still counted in the pie and sums.

**The basket ("Free up")** is a sticky panel: the count of items ticked
(slices, groups, or processes), the **estimated saving** ("frees about
3.1 GB, 12% CPU, approx") from `POST /api/preview`, and **Stop** (primary) and
**Force** (secondary, separate) buttons. Both open a **confirm dialog** listing
exactly which processes will be signalled (children included) and which are
refused; Force's dialog says plainly that unsaved work and databases can be
corrupted. In read-only mode the buttons are disabled with an explanation. After
an action a **toast** shows each outcome and any refusal; the basket clears.

**Live updates:** refresh every 2 s with animated transitions (values glide, no
re-layout flash). Updates **pause** while the pointer is on a slice, a dialog is
open, or a row is being edited, and the header indicator reads "Live" or
"Paused (interacting)". Auto-resume on mouse-out or dialog close; the selected
slice, drill-down level and basket survive refreshes. If the server stops
responding, show a "Connection lost" banner instead of stale data.

**States:** a skeleton ring on first load; empty state for a group with no
processes ("Nothing left, it may have just exited"); inline errors for failed
requests; every control reachable by keyboard with a visible focus ring;
slices are focusable (`role="button"`, `aria-label` "Vivaldi, 4.2 GB, 17%"), Enter
drills, Space adds to the basket, arrow keys move between slices; the live
region announces stop results.

**Safety wording:** the page states "memory is approximate (shared memory is
counted once per process)" next to the figures, and shows "Stop asks the app to
quit; Force ends it immediately" under the buttons.

## Milestones

1. **Tests — complete:** original unit/integration specification unchanged;
   24 additional mocked collector identity tests cover cached PID reuse,
   mid-read replacement, disappearance, and permission-denied defaults.
2. **Logic — complete:** model, grouping, safety, killer, security, snapshot;
   unit tests pass. System-before-app precedence follows the executable spec.
3. **Edges — complete:** collector, HTTP server, CLI; integration tests pass.
   Full suite: **163 passed, no skips**. Invalid rules fail before startup.
   Wheel build includes the inline page and `procs` entry point.
4. **Page — verified in Chromium:** 1440×900 and 800 px screenshots; hover scale
   and dimming, tooltip, group/process/child navigation, breadcrumbs, Esc and
   Backspace, buttons and 1/2/3, keyboard slices, preview, protected controls,
   both dialog cancellations, paused refresh, preserved selection/origin rows,
   reduced motion, connection-loss/recovery, and untrusted text rendering.
   Race and failure checks used fictional processes and recording-only signals.
   Separately authorized real Stop/Force confirmations passed on owned disposable
   workers. Cancelled preview responses cannot overwrite newer dialogs; action
   submission blocks duplicate actions and false cancellation while in flight.
   The fixture page is explicitly labelled TEST FIXTURE.
   Browser checks ran in Chromium through Playwright. Mobile basket overlap and header movement were corrected.
5. **Read-only dry run — technical checks complete; joint review pending:**
   curl snapshots for all three metrics, largest-group and process breakdowns,
   preview, tokenless 403, and read-only Stop/Force 403 passed. GPU was readable.
   Eight `Other:` groups and suspicious labels are recorded with 13 draft rules
   in ignored `artifacts/dry-run-review.md` and `artifacts/proposed-rules.toml`.
   Rules have been parsed and evaluated, but not installed. A live table of
   970 processes, snapshot, and largest-group breakdown took 0.120 s against
   the 2 s refresh interval (one manual performance check, not a soak test).
6. **Real shutdown — verified with explicit approval:** Stop allowed graceful
   cleanup (exit 0); Force produced SIGKILL (exit -9); a worker ignored Stop for
   5 seconds and ended only after separate Force; a three-level tree stopped
   children first with group/PID deduplication. Playwright confirmed both real
   actions on additional owned workers. An unrelated control survived. All QA
   workers were cleaned up and the temporary CLI server exited cleanly on Ctrl-C,
   releasing its listening port. See `docs/VALIDATION.md` for repeatable checks.
7. **Command/docs — complete:** `procs` resolves to the venv entry point; starting it
   with `--no-browser` was verified to enable normal mode, report live OS memory, and exit cleanly
   with a released port on Ctrl-C. The README leads with the everyday `procs` workflow.
8. **Explain — complete:** per-process and selection explanations in normal and read-only
   mode, advisory only. Originally backed by a bundled shared Llama service; replaced in
   milestone 10 by a user-configured OpenAI-compatible endpoint. Tests cover redacted facts,
   token-protected Explain with a fake upstream, and no signals.

9. **Single instance and v1.0 — complete:** `instance.py` holds an exclusive
   non-blocking `flock` for the process lifetime; a second `procs` reports the
   running instance's mode, reopens its URL and exits 0 (or says startup is in
   progress if no URL is published yet). Tests cover refusal before publication,
   private 0600 lock file, stale metadata not blocking restart, release on
   exception, failed startup and clean Ctrl-C. Full suite: 192 passed, no skips.
   Tagged and released as v1.0. Windows was assessed and is unsupported (README).

10. **Configurable Explain and hardening — complete (2026-10-02):** `config.py`, the
    `--config/--explain-url/--explain-model/--allow-remote-explain` flags, prompt override with a fixed
    guard, bearer key from an environment variable, no redirects, loopback ignores proxies, Explain
    hidden when unconfigured, remote host badge. Hardening from the pre-publication review:
    path-based protection of macOS services, `O_NOFOLLOW` lease file with directory owner/mode check,
    repeat launch opens only `http://127.0.0.1:<port>` URLs, per-response CSP nonce (no
    `unsafe-inline` for scripts), token removed from the address bar and fetch URLs, no input echo in
    400s, Explain concurrency cap. Verified: 253 tests pass; the nonce page loaded in Chromium against
    the recording-only fixture with no console errors, no token in the address bar, and Explain hidden.
    Not verified: a real configured endpoint end to end; reload after the token is stripped returns
    403 (re-run `procs`).

Unvalidated: shutdown/data preservation for real user applications and databases, sustained-load performance,
restricted process fields that macOS denies, and browsers other than Chromium.
Unit tests cover missing GPU data and cycle-safe model/origin traversal; the
real machine supplied GPU readings during this run.

## Roadmap after v1

- **v1.1: menu bar app (post-v1, decided 2026-10-02).** Status item with a popover hosting the existing
  page in a `WKWebView` (PyObjC; Python keeps the server and all safety logic), compact layout,
  launch at login, signed and notarized build. Needs its own milestone with test categories first.
  A native-menu-only variant (`rumps`, no donut) is the fallback.

## Out of scope (for now)

Menu bar app (see Roadmap), per-container Docker stats (Docker shows as one group), pause/freeze,
per-app GPU (needs admin rights), saved presets, "Reveal in Finder" (Copy path
is in), dark theme, history graphs, non-macOS support, notifications, auto-update.

## Known limits (state them in the README/page)

Group sizes are scaled estimates (RSS double-counts shared memory; the pie is
scaled to the OS's used figure); macOS only (Windows unsupported, Linux untested); GPU is machine-level only; some processes (other users, restricted) show "n/a" for
path/cwd; an unfamiliar app falls back to "Other: <exe name>" until a rule
is added.
