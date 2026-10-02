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
- The dashboard token is removed from the address bar after load and sent as a header. Reloading the page
  now returns 403; run `procs` again to reopen it.
- The page script uses a per-response CSP nonce instead of `unsafe-inline`.
- Executables under `/usr/libexec/` and `/System/Library/` are protected from Stop and Force.
- At most two Explain requests run at once (HTTP 429 beyond that).

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
