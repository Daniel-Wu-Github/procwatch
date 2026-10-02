# Operational validation

Validated on macOS Apple Silicon. The live collector reported
51,539,607,552 bytes (48 GiB, displayed as GB) and 14 CPU cores, matching
`sysctl -n hw.memsize hw.logicalcpu`. Group values are scaled estimates; the
whole-machine memory total is an OS reading, not a fixture value.

## Setup and automated checks

From the repository root, install and run:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Expected: all tests pass with no skips. This suite uses recording signal functions
and does not signal real processes. The wheel build also passed, with the inline
page and CLI entry point included and private QA artifacts excluded.

## Real read-only API check

Terminal A:

```sh
.venv/bin/procs --read-only --no-browser
```

Terminal B (replace PRINTED_URL with the complete URL from Terminal A):

```sh
.venv/bin/python scripts/read_only_check.py 'PRINTED_URL'
```

Expected: memory/CPU/GPU snapshots, largest-group and process breakdowns, and
preview return 200; tokenless requests and both signal endpoints return 403.
The checker first verifies the page is read-only and refuses a signals-enabled
server. Results go to ignored `artifacts/`. Ctrl-C stops Terminal A. No environment
variables are needed; a restarted server has a new URL/token.

## Opt-in disposable-process shutdown checks

```sh
.venv/bin/python scripts/operational_check.py --allow-real-signals
```

The flag explicitly enables this separate real-signal test. The runner starts its
own CLI server and creates workers in a unique folder under `artifacts/`. It only
submits registered worker PIDs or its own named groups; cleanup checks process
creation times. Ordinary pytest remains recording-only.

Observed results:

| Scenario | Verified outcome |
|---|---|
| Graceful Stop | SIGTERM handler ran, cleanup marker written, exit 0 |
| Force | Exit -9 (SIGKILL), no graceful cleanup marker |
| Worker ignores SIGTERM | Stayed alive for 5 seconds after Stop |
| Separate Force after Stop | Ignoring worker exited -9 |
| Three-level group + duplicate parent PID | Each target once, leaf → branch → parent, all cleanup markers |
| Exited target | Refused as gone, no signal outcome |
| Unrelated disposable control | Stayed alive throughout action checks |
| Preview | Correct target list; worker stayed alive |
| Missing token, foreign Origin, foreign Host | 403; control stayed alive |
| Malformed action body | 400; control stayed alive |
| CLI Ctrl-C | Exit 0 and closed listening socket |
| CLI invalid or occupied port | Clear argparse error, nonzero exit, no traceback |

The normal runner cleans up all its remaining disposable workers and its server
in a finally block. Failed assertions exit nonzero. It writes token-free check
results under its printed artifacts directory; its separate browser-session file
contains a local token and is ignored by Git.

## Playwright checks

For mock UX checks, start the fixture and open its URL in a separate browser tab:

```sh
.venv/bin/python scripts/browser_fixture.py
```

It says **TEST FIXTURE** in the title, header, and explanation: simulated 8 GB,
fictional processes, no real signals. Keep the real read-only page in its own tab.
Run `scripts/browser_dialog_regression.js` with Playwright's run-code facility. It locates the
already-open fixture tab and uses temporary pages that close after the check.

The regression failed before the fix and passes afterward:

- Cancel Stop while preview is delayed; open Force; resolve the old response last.
  Only the latest Force target list is displayed and submitted.
- Hold an action POST. Stop, Force, Confirm, and Cancel are disabled; Escape cannot
  pretend the submitted action was cancelled; only one action is dispatched.

Additional checked UX: hover 1.06×/other opacity 0.6, tooltip, paused refresh,
group/process/child drill-down, breadcrumbs, Esc/Backspace, 1/2/3 and metric buttons,
keyboard arrows/Space/Enter/Tab, origin rows and selection surviving refresh,
protected controls, read-only disabled actions, reduced motion, responsive layouts
at 1440×900 and 800 px, and HTML-looking names displayed as text. Connection failure
shows a banner and recovers on the next successful refresh. An aborted action POST
explains uncertain delivery and requires closing/re-previewing before a retry.

For the separately authorized real-browser action check, run:

```sh
.venv/bin/python scripts/operational_check.py --allow-real-signals --browser
```

After API assertions pass, the runner prints a temporary live URL and the exact
`qa-browser-stop`, `qa-browser-force`, and control PIDs. Use only those named
workers in Playwright. Stop the graceful worker; Stop the stubborn worker, verify
it remains, then Force it. Confirm lists must contain only the printed target PID.
Once done, create `finish-browser` in the printed session folder. The runner checks
exit codes, cleanup markers, and the surviving control, then cleans up its server.
Do not run this against another server or choose unrelated groups.

These real UI actions passed: graceful worker exit 0; stubborn worker received
SIGTERM, stayed alive, then exited -9 after Force. Outcome toasts matched each PID.
The fixture was closed with zero recorded signals; intercepted regression actions
were handled entirely by Playwright routes.

## Limits and remaining review

No real user application or database was used as a shutdown target. Those apps'
data preservation and shutdown behavior remain unvalidated, as do other browser
engines and sustained-load performance. Restricted macOS fields remain unavailable.

Joint grouping-rule review is optional. Screenshots, detailed API responses and grouping
proposals belong in the ignored `artifacts/` directory; no live token or machine snapshot belongs in a commit.

## Explain

Explain is optional and off until an endpoint is configured (see the README). It is covered by
tests with fake upstream endpoints: redacted facts (no command lines or usernames), a custom
prompt that keeps the fixed safety guard, bearer key and model fields, refused redirects,
loopback requests ignoring proxy variables, the remote-endpoint opt-in, and the concurrency cap.
A fake read-only fixture verified that Explain works without signal permission and that model text
containing script tags is displayed literally without executing. Not covered by automated tests:
a real model server end to end.
