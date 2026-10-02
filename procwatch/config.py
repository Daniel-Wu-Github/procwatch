"""config.toml: where Explain sends its redacted process facts. Explain is off until configured."""
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

DEFAULT_CONFIG_PATH = Path('~/.config/procwatch/config.toml')
MAX_PROMPT_BYTES = 8192
_EXPLAIN_KEYS = {'base_url', 'model', 'prompt_file', 'api_key_env', 'allow_remote'}


class ConfigError(ValueError):
    pass


def _is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ExplainConfig:
    base_url: str | None = None
    model: str | None = None
    prompt: str | None = None
    api_key: str | None = field(default=None, repr=False)
    allow_remote: bool = False

    @property
    def enabled(self):
        return self.base_url is not None

    @property
    def host(self):
        return urlsplit(self.base_url).hostname if self.base_url else None

    @property
    def remote(self):
        return self.enabled and not _is_loopback(self.host)

    @property
    def endpoint(self):
        return self.base_url.rstrip('/') + '/chat/completions' if self.base_url else None


def _check_url(url, allow_remote):
    if not isinstance(url, str) or not url:
        raise ConfigError('base_url must be a non-empty string')
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port
    except ValueError as exc:
        raise ConfigError(f'base_url is not a valid URL: {exc}') from exc
    if parts.scheme not in ('http', 'https') or not host:
        raise ConfigError('base_url must be an http(s) URL with a host')
    if parts.username is not None or parts.password is not None:
        raise ConfigError('base_url must not contain credentials; use api_key_env')
    if parts.query or parts.fragment:
        raise ConfigError('base_url must not contain a query or fragment')
    if not _is_loopback(host):
        if not allow_remote:
            raise ConfigError(f'base_url points at {host}, which would send process names and paths off this Mac; '
                              'set allow_remote = true (or --allow-remote-explain) to allow it')
        if parts.scheme != 'https':
            raise ConfigError('a remote base_url must use https')


def load_explain_config(path, *, url=None, model=None, allow_remote=False, environ=None):
    environ = os.environ if environ is None else environ
    values = {}
    path = Path(path).expanduser() if path is not None else None
    if path is not None and path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f'cannot read {path}: {exc}') from exc
        unknown = set(data) - {'explain'}
        if unknown:
            raise ConfigError(f'unknown table(s) in {path}: {", ".join(sorted(unknown))}')
        values = data.get('explain', {})
        if not isinstance(values, dict):
            raise ConfigError('[explain] must be a table')
        unknown = set(values) - _EXPLAIN_KEYS
        if unknown:
            raise ConfigError(f'unknown key(s) in [explain]: {", ".join(sorted(unknown))}'
                              + ('; keys are never stored in the file, use api_key_env' if 'api_key' in unknown else ''))
    base_url = url if url is not None else values.get('base_url')
    model = model if model is not None else values.get('model')
    if not isinstance(values.get('allow_remote', False), bool):
        raise ConfigError('allow_remote must be true or false')
    permitted = values.get('allow_remote', False) or allow_remote
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigError('model must be a non-empty string')
    if base_url is None:
        if model is not None:
            raise ConfigError('model is set but base_url is not; set base_url (or --explain-url)')
        return ExplainConfig()
    _check_url(base_url, permitted)
    prompt = None
    if 'prompt_file' in values:
        prompt = _read_prompt(values['prompt_file'], path)
    bearer = None
    if 'api_key_env' in values:
        name = values['api_key_env']
        if not isinstance(name, str) or not environ.get(name):
            raise ConfigError(f'api_key_env names {name!r}, which is not set in the environment')
        bearer = environ[name]
    return ExplainConfig(base_url=base_url, model=model, prompt=prompt, api_key=bearer, allow_remote=permitted)


def _read_prompt(name, config_path):
    if not isinstance(name, str) or not name:
        raise ConfigError('prompt_file must be a path string')
    target = Path(name).expanduser()
    if not target.is_absolute() and config_path is not None:
        target = config_path.parent / target
    try:
        raw = target.read_bytes()
        text = raw.decode().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f'prompt_file {target} cannot be read: {exc}') from exc
    if len(raw) > MAX_PROMPT_BYTES:
        raise ConfigError(f'prompt_file {target} is larger than {MAX_PROMPT_BYTES} bytes')
    if not text:
        raise ConfigError(f'prompt_file {target} is empty')
    return text
