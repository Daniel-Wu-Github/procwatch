import argparse
from contextlib import ExitStack
import getpass
import os
from pathlib import Path
import secrets
import webbrowser
from urllib.parse import urlsplit

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_explain_config
from .grouping import RulesError, parse_rules
from .instance import Instance
from .server import create_server


def _loopback_url(url):
    try:
        parts = urlsplit(url)
        return parts.scheme == 'http' and parts.hostname == '127.0.0.1' and parts.port is not None
    except (ValueError, TypeError, AttributeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='See where your Mac resources go, then choose what to stop.')
    parser.add_argument('--read-only', action='store_true', help='Disable all signals (use on the first run)')
    parser.add_argument('--rules', type=Path, default=Path('~/.config/procwatch/rules.toml'))
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH, help='Explain settings (default ~/.config/procwatch/config.toml)')
    parser.add_argument('--explain-url', help='OpenAI-compatible base URL for Explain, e.g. http://127.0.0.1:11434/v1')
    parser.add_argument('--explain-model', help='Model name to send to the Explain endpoint')
    parser.add_argument('--allow-remote-explain', action='store_true', help='Allow a non-loopback https Explain endpoint')
    parser.add_argument('--projects-root', type=Path, default=Path('~/projects'))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    try:
        path = args.rules.expanduser()
        rules = parse_rules(path.read_text()) if path.exists() else []
    except (OSError, RulesError) as exc:
        parser.error(f'Unable to load rules: {exc}')
    try:
        explain = load_explain_config(args.config, url=args.explain_url, model=args.explain_model,
                                      allow_remote=args.allow_remote_explain)
    except ConfigError as exc:
        parser.error(f'Unable to load config: {exc}')
    with ExitStack() as stack:
        try:
            instance = stack.enter_context(Instance())
        except OSError as exc:
            parser.error(f'Unable to open the instance lease: {exc}')
        if not instance.acquire():
            existing = instance.read() or {}
            print('procwatch is already running' + (' in read-only mode.' if existing.get('read_only') else ' in normal mode.'), flush=True)
            url = existing.get('url')
            if _loopback_url(url):
                print(url, flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
            elif url:
                print('The running instance published an unusable URL; stop it and run procs again.', flush=True)
            else:
                print('Startup is in progress; run procs again in a moment.', flush=True)
            print('Stop the existing instance before changing its mode or startup options.', flush=True)
            return 0
        return run(args, rules, parser, instance, explain)


def run(args, rules, parser, instance, explain):
    from .collector import collect, machine
    token = secrets.token_urlsafe(32)
    try:
        server = create_server(collect_fn=collect, machine_fn=machine, signal_fn=os.kill, token=token,
                               projects_root=str(args.projects_root.expanduser().resolve()), rules=rules,
                               me=getpass.getuser(), self_pid=os.getpid(), allow_kill=not args.read_only, port=args.port,
                               explain_config=explain)
    except OSError as exc:
        parser.error(f'Unable to start server: {exc}')
    url = f'http://127.0.0.1:{server.server_address[1]}/?token={token}'
    instance.publish(dict(url=url, read_only=args.read_only, pid=os.getpid()))
    print(url, flush=True)
    print('Read-only mode: signals disabled.' if args.read_only else 'Stop sends SIGTERM; Force sends SIGKILL.', flush=True)
    if explain.remote:
        print(f'Explain sends process names and paths to {explain.host}.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nprocwatch stopped.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
