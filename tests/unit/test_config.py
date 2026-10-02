"""config.toml: where Explain sends its (redacted) process facts. The tests are the spec."""
import pytest

from procwatch.config import ConfigError, ExplainConfig, load_explain_config


def write(tmp_path, text, name='config.toml'):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_missing_file_and_no_flags_leaves_explain_disabled(tmp_path):
    config = load_explain_config(tmp_path / 'absent.toml')
    assert config == ExplainConfig()
    assert not config.enabled and not config.remote and config.host is None


def test_loopback_endpoint_is_enabled_without_extra_permission(tmp_path):
    path = write(tmp_path, '[explain]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.1"\n')
    config = load_explain_config(path)
    assert config.enabled and not config.remote
    assert config.endpoint == 'http://127.0.0.1:11434/v1/chat/completions'
    assert config.model == 'llama3.1' and config.host == '127.0.0.1'


@pytest.mark.parametrize('base', ['http://localhost:1234/v1', 'http://[::1]:8000/v1', 'http://127.0.0.1:8011/v1/'])
def test_loopback_hosts_and_trailing_slash_are_normalised(tmp_path, base):
    config = load_explain_config(write(tmp_path, f'[explain]\nbase_url = "{base}"\n'))
    assert not config.remote and config.endpoint.endswith('/v1/chat/completions')
    assert '//chat' not in config.endpoint


def test_remote_endpoint_requires_https_and_explicit_permission(tmp_path):
    https = '[explain]\nbase_url = "https://api.example.com/v1"\n'
    with pytest.raises(ConfigError, match='allow_remote'):
        load_explain_config(write(tmp_path, https))
    config = load_explain_config(write(tmp_path, https + 'allow_remote = true\n', 'ok.toml'))
    assert config.remote and config.host == 'api.example.com'
    with pytest.raises(ConfigError, match='https'):
        load_explain_config(write(tmp_path, '[explain]\nbase_url = "http://api.example.com/v1"\nallow_remote = true\n', 'plain.toml'))


def test_loopback_lookalike_host_is_remote(tmp_path):
    with pytest.raises(ConfigError, match='allow_remote'):
        load_explain_config(write(tmp_path, '[explain]\nbase_url = "https://127.0.0.1.evil.example/v1"\n'))
    with pytest.raises(ConfigError, match='allow_remote'):
        load_explain_config(write(tmp_path, '[explain]\nbase_url = "https://localhost.evil.example/v1"\n', 'b.toml'))


@pytest.mark.parametrize('base', ['ftp://127.0.0.1/v1', 'file:///etc/passwd', 'not a url', 'http://',
                                  'http://user:pw@127.0.0.1:8011/v1', 'http://127.0.0.1:8011/v1?x=1', 'http://127.0.0.1:8011/v1#frag'])
def test_unusable_or_credential_bearing_urls_are_rejected(tmp_path, base):
    with pytest.raises(ConfigError):
        load_explain_config(write(tmp_path, f'[explain]\nbase_url = "{base}"\n'))


def test_unknown_keys_and_tables_are_errors_not_ignored(tmp_path):
    with pytest.raises(ConfigError, match='base_ulr'):
        load_explain_config(write(tmp_path, '[explain]\nbase_ulr = "http://127.0.0.1:1/v1"\n'))
    with pytest.raises(ConfigError, match='surprise'):
        load_explain_config(write(tmp_path, '[surprise]\nx = 1\n', 'b.toml'))


def test_wrong_types_and_invalid_toml_are_errors(tmp_path):
    for body in ('[explain]\nbase_url = 5\n', '[explain]\nbase_url = "http://127.0.0.1:1/v1"\nallow_remote = "yes"\n',
                 '[explain]\nmodel = ""\n', '[explain\n'):
        with pytest.raises(ConfigError):
            load_explain_config(write(tmp_path, body))


def test_prompt_file_is_read_and_must_exist_and_be_small(tmp_path):
    (tmp_path / 'prompt.txt').write_text('Be brief. Mention what a gamer loses by stopping it.\n')
    body = '[explain]\nbase_url = "http://127.0.0.1:1/v1"\nprompt_file = "prompt.txt"\n'
    config = load_explain_config(write(tmp_path, body))
    assert config.prompt == 'Be brief. Mention what a gamer loses by stopping it.'
    with pytest.raises(ConfigError, match='prompt_file'):
        load_explain_config(write(tmp_path, body.replace('prompt.txt', 'missing.txt'), 'b.toml'))
    (tmp_path / 'big.txt').write_text('x' * 9000)
    with pytest.raises(ConfigError, match='prompt_file'):
        load_explain_config(write(tmp_path, body.replace('prompt.txt', 'big.txt'), 'c.toml'))
    (tmp_path / 'empty.txt').write_text('  \n')
    with pytest.raises(ConfigError, match='prompt_file'):
        load_explain_config(write(tmp_path, body.replace('prompt.txt', 'empty.txt'), 'd.toml'))


def test_api_key_comes_from_the_named_environment_variable_only(tmp_path):
    body = '[explain]\nbase_url = "http://127.0.0.1:1/v1"\napi_key_env = "MY_EXPLAIN_KEY"\n'
    path = write(tmp_path, body)
    assert load_explain_config(path, environ={'MY_EXPLAIN_KEY': 'sk-test'}).api_key == 'sk-test'
    with pytest.raises(ConfigError, match='MY_EXPLAIN_KEY'):
        load_explain_config(path, environ={})
    assert 'sk-test' not in repr(load_explain_config(path, environ={'MY_EXPLAIN_KEY': 'sk-test'}))
    with pytest.raises(ConfigError, match='api_key'):
        load_explain_config(write(tmp_path, '[explain]\nbase_url = "http://127.0.0.1:1/v1"\napi_key = "sk-literal"\n', 'b.toml'))


def test_flags_override_the_file(tmp_path):
    path = write(tmp_path, '[explain]\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "from-file"\n')
    config = load_explain_config(path, url='http://localhost:2/v1', model='from-flag')
    assert config.endpoint == 'http://localhost:2/v1/chat/completions' and config.model == 'from-flag'


def test_flag_alone_enables_explain_and_remote_flag_grants_permission(tmp_path):
    config = load_explain_config(tmp_path / 'absent.toml', url='http://127.0.0.1:3/v1')
    assert config.enabled
    with pytest.raises(ConfigError, match='allow_remote'):
        load_explain_config(tmp_path / 'absent.toml', url='https://api.example.com/v1')
    assert load_explain_config(tmp_path / 'absent.toml', url='https://api.example.com/v1', allow_remote=True).remote


def test_model_flag_without_any_url_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match='base_url'):
        load_explain_config(tmp_path / 'absent.toml', model='x')
