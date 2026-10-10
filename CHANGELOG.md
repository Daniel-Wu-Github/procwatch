# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-10-10

First public release.

### Added
- **Dashboard:** a donut of the whole machine for memory, CPU and GPU (GPU is machine-level only), with hover,
  drill-down into groups, processes and child processes, and live updates that pause while you interact. The page
  stops polling while its tab is hidden, and its checkbox column has a screen-reader label (axe-core reports no
  WCAG 2.1 A/AA or best-practice violations).
- **Free up:** a basket with an estimated saving, then Stop (SIGTERM) and Force (SIGKILL) as separate, confirmed
  actions. Stop never escalates to Force.
- **Where a process came from:** app or project folder, executable, working directory, command line and parent chain.
  Grouping is automatic and editable through `rules.toml`.
- **Explain (optional, off by default):** point it at any OpenAI-compatible chat endpoint (Ollama, LM Studio, a hosted
  API) through `~/.config/procwatch/config.toml` or `--explain-url`, `--explain-model` and `--allow-remote-explain`.
  Optional custom prompt file (a fixed safety guard is always appended) and an API key read from an environment
  variable. A non-loopback endpoint needs explicit opt-in and https, and is flagged in the UI. Command lines and
  usernames are never sent. At most two Explain requests run at once.
- **Commands:** `procwatch`, with `procs` as a shorter alias (the Homebrew `procs` tool uses that name too).
  `--read-only` disables Stop and Force on the server.
- **One session per terminal:** `procwatch` stops cleanly on Ctrl-C, SIGTERM, a closed terminal (SIGHUP) or when the
  last browser tab closes (a reload gets a short grace period). Ctrl-C does not wait for a slow Explain request, and an
  in-flight Stop or Force gets up to 10 s to finish and report. A second `procwatch` reuses the running one. A stale
  link shows a short "link expired" page.
- **Project files:** `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, issue and PR templates, a macOS CI workflow,
  a demo GIF and screenshots. Python 3.12 to 3.14 are supported.

### Safety and security
- Stop and Force signal exactly the targets you confirmed. A preview returns each target's start time, the dashboard
  sends them back, and the server skips any pid whose start time changed (reused by another process). Processes spawned
  after the preview are not signalled. The process table is read once per action, and one unexpected error does not
  abort the rest of a batch. The old `pids`/`groups` body is rejected with HTTP 400, so the stale-click guard cannot
  be bypassed.
- Never signalled: processes you do not own, pid 0 and 1, procwatch itself, its terminal and shell chain (captured at
  startup), protected macOS processes, and executables under `/usr/libexec/`, `/usr/sbin/`, `/sbin/`,
  `/System/Library/`, system Cryptex paths and `/Library/Apple/`.
- The server binds `127.0.0.1` only and requires a per-run token and a loopback `Host`; POSTs also need a same-origin
  `Origin`. The page uses a per-response CSP nonce (no `unsafe-inline` for scripts) and sends its token as a header.
- The instance lock file is opened with `O_NOFOLLOW` in a private directory, a repeat launch opens only
  `http://127.0.0.1:<port>` URLs, and bad input is not echoed back in error responses.
