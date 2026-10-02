"""Hardening from the pre-publication security review: protected paths, lease file, launch URL."""
import os
from types import SimpleNamespace

import pytest

from procwatch import cli
from procwatch.instance import Instance
from procwatch.safety import check

KW = dict(me='tester', self_pid=500, ancestors_of_self=frozenset())


@pytest.mark.parametrize('exe', ['/usr/libexec/cfprefsd', '/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter',
                                 '/System/Library/PrivateFrameworks/Foo.framework/Support/foo'])
def test_user_owned_macos_services_are_protected_by_path(proc, exe):
    verdict = check(proc(10, name='ControlCenter', exe=exe), **KW)
    assert not verdict.allowed and 'macOS' in verdict.reason


def test_apple_apps_and_unknown_paths_remain_stoppable(proc):
    assert check(proc(10, name='Music', exe='/System/Applications/Music.app/Contents/MacOS/Music'), **KW).allowed
    assert check(proc(11, name='node', exe='/opt/homebrew/bin/node'), **KW).allowed
    assert check(proc(12, name='mystery', exe=None), **KW).allowed


def test_lookalike_prefixes_are_not_protected(proc):
    assert check(proc(10, name='x', exe='/usr/libexec-fake/x'), **KW).allowed
    assert check(proc(11, name='x', exe='/Users/me/System/Library/x'), **KW).allowed


def test_lease_file_symlink_is_refused_and_target_untouched(tmp_path):
    target = tmp_path / 'victim'
    target.write_text('keep me')
    os.chmod(target, 0o644)
    lease = tmp_path / 'lease'
    lease.mkdir()
    os.symlink(target, lease / 'instance.lock')
    with pytest.raises(OSError):
        with Instance(lease):
            pass
    assert target.read_text() == 'keep me' and target.stat().st_mode & 0o777 == 0o644


def test_existing_lease_directory_is_tightened_to_private(tmp_path):
    lease = tmp_path / 'lease'
    lease.mkdir(mode=0o755)
    os.chmod(lease, 0o755)
    with Instance(lease):
        pass
    assert lease.stat().st_mode & 0o777 == 0o700


def test_cli_reports_a_refused_lease_file_without_a_traceback(monkeypatch, tmp_path, capsys):
    lease = tmp_path / 'lease'
    lease.mkdir()
    os.symlink(tmp_path / 'elsewhere', lease / 'instance.lock')
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(lease))
    with pytest.raises(SystemExit):
        cli.main(['--no-browser', '--rules', str(tmp_path / 'no-rules')])
    assert 'lease' in capsys.readouterr().err.lower()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://evil.example/', 'http://evil.example:1/', 'http://127.0.0.1.evil.example:1/',
                                 'javascript:alert(1)', 'http://127.0.0.1/', 'x-apple.systempreferences:'])
def test_repeat_launch_never_opens_a_non_loopback_lease_url(monkeypatch, tmp_path, capsys, url):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path))
    with Instance(tmp_path) as running:
        assert running.acquire()
        running.publish({'url': url, 'read_only': False})
        opened = []
        monkeypatch.setattr(cli.webbrowser, 'open', opened.append)
        assert cli.main(['--rules', str(tmp_path / 'no-rules')]) == 0
    assert opened == [] and url not in capsys.readouterr().out


def test_repeat_launch_still_opens_a_genuine_loopback_url(monkeypatch, tmp_path):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path))
    with Instance(tmp_path) as running:
        assert running.acquire()
        running.publish({'url': 'http://127.0.0.1:4321/?token=abc', 'read_only': False})
        opened = []
        monkeypatch.setattr(cli.webbrowser, 'open', opened.append)
        assert cli.main(['--rules', str(tmp_path / 'no-rules')]) == 0
    assert opened == ['http://127.0.0.1:4321/?token=abc']
