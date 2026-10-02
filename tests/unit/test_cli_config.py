"""CLI wiring for the Explain config. A fake server records what create_server received."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from procwatch import cli


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv('PROCWATCH_INSTANCE_DIR', str(tmp_path / 'lease'))
    seen = {}
    def interrupt(): raise KeyboardInterrupt
    def fake(**kw):
        seen.update(kw)
        return SimpleNamespace(server_address=('127.0.0.1', 1234), serve_forever=interrupt, server_close=lambda: None)
    monkeypatch.setattr(cli, 'create_server', fake)
    def go(*argv):
        return cli.main(['--no-browser', '--rules', str(tmp_path / 'no-rules'), *argv])
    return go, seen, tmp_path


def test_no_config_means_explain_disabled(run):
    go, seen, _ = run
    assert go() == 0
    assert not seen['explain_config'].enabled


def test_default_config_path_is_under_the_users_config_dir(run, monkeypatch, tmp_path):
    go, seen, _ = run
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    config_dir = tmp_path / 'home/.config/procwatch'
    config_dir.mkdir(parents=True)
    (config_dir / 'config.toml').write_text('[explain]\nbase_url = "http://127.0.0.1:8011/v1"\n')
    assert go() == 0
    assert seen['explain_config'].endpoint == 'http://127.0.0.1:8011/v1/chat/completions'


def test_flags_and_explicit_config_path(run):
    go, seen, tmp = run
    path = tmp / 'mine.toml'
    path.write_text('[explain]\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "file-model"\n')
    assert go('--config', str(path), '--explain-model', 'flag-model') == 0
    assert seen['explain_config'].model == 'flag-model'
    assert go('--explain-url', 'http://localhost:2/v1') == 0
    assert seen['explain_config'].endpoint == 'http://localhost:2/v1/chat/completions'


def test_invalid_config_exits_before_any_server_starts(run, capsys):
    go, seen, tmp = run
    bad = tmp / 'bad.toml'
    bad.write_text('[explain]\nbase_ulr = "x"\n')
    with pytest.raises(SystemExit):
        go('--config', str(bad))
    assert 'base_ulr' in capsys.readouterr().err and not seen


def test_remote_endpoint_needs_the_flag_and_is_announced(run, capsys):
    go, seen, _ = run
    with pytest.raises(SystemExit):
        go('--explain-url', 'https://api.example.com/v1')
    assert go('--explain-url', 'https://api.example.com/v1', '--allow-remote-explain') == 0
    assert 'api.example.com' in capsys.readouterr().out
    assert seen['explain_config'].remote
