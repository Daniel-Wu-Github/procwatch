# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Configurable Explain: point it at any OpenAI-compatible chat endpoint (Ollama, LM Studio, a hosted API)
  through `~/.config/procwatch/config.toml` or `--explain-url`, `--explain-model`, `--allow-remote-explain`.
  Off until configured. Optional custom prompt file (a fixed safety guard is always appended) and an API key
  read from an environment variable. A non-loopback endpoint needs explicit opt-in, https, and is flagged in the UI.
- Demo GIF and screenshots, `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md` and a CI workflow.

### Changed
- Stop and Force now signal exactly the processes you confirmed. The preview returns each target's start time, the
  page sends them back, and a pid whose start time changed (reused by another process) is skipped; processes spawned
  after the preview are not signalled. The process table is read once per action, and one unexpected error no longer
  aborts the rest of a batch. On exit, an in-flight Stop/Force is given up to 10 s to finish and report.
- More system paths are protected: `/usr/sbin/`, `/sbin/`, Cryptex system libexec/OS paths and `/Library/Apple/`.
  The terminal chain above procwatch is captured at startup so it stays protected.
- `procs` is now one session per terminal: it stops cleanly on Ctrl-C, SIGTERM, a closed terminal (SIGHUP), or when
  the last browser tab closes (a reload is allowed a short grace period). Ctrl-C no longer waits for a slow Explain request.
- The page sends its token as a header instead of in fetch URLs. A stale link (procwatch restarted) shows a short
  "link expired" page instead of bare JSON.
- The page script uses a per-response CSP nonce instead of `unsafe-inline`.
- Executables under `/usr/libexec/` and `/System/Library/` are protected from Stop and Force.
- At most two Explain requests run at once (HTTP 429 beyond that).
- The Explain safety guard now also tells the model to describe only the listed processes and never invent others.

### Fixed
- The instance lock file is opened with `O_NOFOLLOW` and its directory forced to private permissions.
- A repeat `procs` opens only `http://127.0.0.1:<port>` URLs from the lease file.
- Bad query or `Content-Length` input is no longer echoed back in error responses.

## [1.0.0]

- Donut view of memory, CPU and GPU with hover, drill-down into processes and children, and a "Free up"
  basket with separate Stop (SIGTERM) and Force (SIGKILL).
- Per-process origin: app or project folder, executable, working directory, command line, parent chain.
- Local server on `127.0.0.1` with per-run token, Host and Origin checks, `--read-only` mode, a single-instance
  lease, and an editable `rules.toml` for grouping.
