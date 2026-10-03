"""Packaging metadata, the second command name, and static properties of the dashboard page."""
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / 'pyproject.toml').read_text())
PAGE = (ROOT / 'procwatch/page.html').read_text()


def test_both_command_names_start_the_same_program():
    scripts = PYPROJECT['project']['scripts']
    assert scripts['procwatch'] == 'procwatch.cli:main' and scripts['procs'] == 'procwatch.cli:main'


def test_supported_python_versions_are_declared():
    classifiers = PYPROJECT['project']['classifiers']
    for version in ('3.12', '3.13', '3.14'):
        assert f'Programming Language :: Python :: {version}' in classifiers


def test_readme_leads_with_the_procwatch_command_and_still_mentions_procs():
    readme = (ROOT / 'README.md').read_text()
    install = readme[readme.index('## Install'):readme.index('## Everyday use')]
    assert 'procwatch --read-only' in install and '`procs`' in readme


def test_checkbox_column_header_has_text_for_screen_readers():
    assert "sr-only" in PAGE and "'Select'" in PAGE


def test_page_stops_polling_while_hidden_and_refreshes_when_shown_again():
    assert 'document.hidden' in PAGE and "addEventListener('visibilitychange'" in PAGE
