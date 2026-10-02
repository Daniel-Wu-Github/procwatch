from types import SimpleNamespace
import pytest
from procwatch import cli
from procwatch.instance import Instance


def test_repeat_command_reuses_existing_url_and_reports_actual_mode(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path))
    with Instance(tmp_path) as running:
        assert running.acquire()
        running.publish({'url': 'http://127.0.0.1:1234/', 'read_only': True})
        monkeypatch.setattr(cli, 'create_server', lambda **kw: pytest.fail('Created duplicate server'))
        opened = []
        monkeypatch.setattr(cli.webbrowser, 'open', opened.append)
        assert cli.main(['--port', '5555', '--rules', str(tmp_path/'no-rules')]) == 0
        assert opened == ['http://127.0.0.1:1234/']
        assert 'read-only' in capsys.readouterr().out


def test_failed_startup_releases_lease(monkeypatch, tmp_path):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path))
    def failed(**kw): raise OSError('address unavailable')
    monkeypatch.setattr(cli, 'create_server', failed)
    with pytest.raises(SystemExit):
        cli.main(['--no-browser', '--rules', str(tmp_path/'no-rules')])
    with Instance(tmp_path) as retry:
        assert retry.acquire()


def test_clean_shutdown_releases_lease_and_closes_server(monkeypatch, tmp_path):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path))
    closed = []
    def interrupt(): raise KeyboardInterrupt
    server = SimpleNamespace(server_address=('127.0.0.1', 1234), serve_forever=interrupt,
                             server_close=lambda: closed.append(True))
    monkeypatch.setattr(cli, 'create_server', lambda **kw: server)
    assert cli.main(['--no-browser', '--rules', str(tmp_path/'no-rules')]) == 0
    assert closed == [True]
    with Instance(tmp_path) as retry:
        assert retry.acquire()
