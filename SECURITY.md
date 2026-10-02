# Security policy

procwatch can send SIGTERM and SIGKILL to your processes, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private reporting:
the repository's **Security** tab, then **Report a vulnerability**. Include the version
(`pip show procwatch`), your macOS version, and steps to reproduce.

You can expect an acknowledgement within about a week. This is a hobby project maintained by one
person, so there is no formal SLA, but confirmed issues are fixed before anything else.

## What counts

Examples of in-scope problems:

- A way to make procwatch signal a process it must refuse: another user's process, pid 0 or 1,
  procwatch itself, its parent chain, or a protected macOS process.
- Bypassing the per-run token, the `Host` check or the same-origin `Origin` check on the local server.
- Any way for a web page or another local user to read procwatch data or trigger a signal.
- Process names, paths or command lines (untrusted input) executing script in the dashboard.
- Explain sending more than documented (command lines, usernames) or to a host the user did not allow.
- Signals other than SIGTERM and SIGKILL, or automatic escalation from Stop to Force.

Out of scope: needing root (procwatch never asks for it), attacks that already run as your user
with full control of your account, and the accuracy of a model's advisory text.

## Design summary

- The server binds `127.0.0.1` only. Every request needs a random per-run token and a loopback
  `Host`; POSTs also need a same-origin `Origin`.
- `--read-only` disables Stop and Force on the server, not just in the UI.
- Explain is off until you configure an endpoint. A non-loopback endpoint needs explicit opt-in and https.

See the "Safety" section of [PLAN.md](PLAN.md) for the full list.
