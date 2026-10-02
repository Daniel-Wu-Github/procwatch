# Contributing

Thanks for taking a look. procwatch is a small macOS tool, and issues and pull requests are welcome.
It is maintained by one person, so replies may be slow and there is no support guarantee.

## Setup

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Requires macOS (Apple Silicon is the verified platform). Windows is not supported and Linux is untested.

## Ground rules

- **Never signal a real process in a test.** Tests use a recording `signal_fn`. If you need to try
  Stop or Force by hand, start a harmless process yourself (for example `sleep 600`) and target only that.
- **Tests first.** Add a failing test in `tests/unit` or `tests/integration`, then the change. Please do not
  edit an existing test just to make it pass; if a test looks wrong, say so in the pull request.
- **Untrusted text stays inert.** Process names, paths and command lines go through `textContent`, never `innerHTML`.
  The UI is vanilla JS and inline SVG with no CDN or external libraries.
- **Do not weaken the server's checks** (loopback bind, token, Host, Origin) or the refusal list in `procwatch/safety.py`.
- Only SIGTERM (Stop) and SIGKILL (Force); no automatic escalation. No `sudo` or `powermetrics`.
- Keep the design in [PLAN.md](PLAN.md) in sync with behaviour changes.

## Pull requests

Describe what changed and how you verified it (which tests ran, and anything you could not run).
Security issues should be reported privately; see [SECURITY.md](SECURITY.md).
