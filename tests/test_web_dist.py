"""The committed web bundle: the hash, the guard, and the duplicación in Node.

`web/dist` is committed so `sigil serve` needs no Node, which makes one bug
possible: sources change, the bundle does not. The guard is a hash, and a hash is
only worth anything if the two implementations agree. These tests pin the recipe,
compare it with `web/scripts/source-hash.mjs` when Node exists, and check that the
bundle in the repository is the one the sources produce.

One thing the bundle is *not*: a requirement of `sigil server`. That surface is the
API alone, so its parser has no web flag and its configuration invents no password.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.server import webbuild

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'


def test_web_root_is_the_checkout() -> None:
    assert webbuild.web_root() == WEB


def test_hash_is_stable_for_the_same_tree(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'index.html').write_text('<html></html>', encoding='utf-8')
    (tmp_path / 'src' / 'main.tsx').write_text('export {}\n', encoding='utf-8')
    assert webbuild.source_hash(tmp_path) == webbuild.source_hash(tmp_path)
    assert len(webbuild.source_hash(tmp_path)) == webbuild.HASH_CHARS


def test_hash_changes_when_content_changes(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    entry = tmp_path / 'src' / 'main.tsx'
    entry.write_text('export {}\n', encoding='utf-8')
    before = webbuild.source_hash(tmp_path)
    entry.write_text('export const x = 1\n', encoding='utf-8')
    assert webbuild.source_hash(tmp_path) != before


def test_hash_changes_when_a_file_is_renamed(tmp_path: Path) -> None:
    """The path is part of the digest: the browser fetches by name."""
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'a.ts').write_text('export {}\n', encoding='utf-8')
    before = webbuild.source_hash(tmp_path)
    (tmp_path / 'src' / 'a.ts').rename(tmp_path / 'src' / 'b.ts')
    assert webbuild.source_hash(tmp_path) != before


def test_ignored_directories_do_not_count(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'main.tsx').write_text('export {}\n', encoding='utf-8')
    before = webbuild.source_hash(tmp_path)
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules' / 'x.js').write_text('installed', encoding='utf-8')
    (tmp_path / 'dist').mkdir()
    (tmp_path / 'dist' / 'build.json').write_text('{}', encoding='utf-8')
    assert webbuild.source_hash(tmp_path) == before


def test_staleness_says_the_bundle_is_missing(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'main.tsx').write_text('export {}\n', encoding='utf-8')
    state = webbuild.staleness(tmp_path)
    assert state.stale
    assert 'not built' in state.reason
    assert 'webbuild --build' in state.message()


def test_staleness_says_the_manifest_is_missing(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'main.tsx').write_text('export {}\n', encoding='utf-8')
    (tmp_path / 'dist').mkdir()
    (tmp_path / 'dist' / 'index.html').write_text('<html></html>', encoding='utf-8')
    state = webbuild.staleness(tmp_path)
    assert state.stale
    assert 'build.json' in state.reason


def test_staleness_is_fresh_when_the_manifest_matches(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'main.tsx').write_text('export {}\n', encoding='utf-8')
    (tmp_path / 'dist').mkdir()
    current = webbuild.source_hash(tmp_path)
    (tmp_path / 'dist' / 'build.json').write_text(f'{{"hash": "{current}"}}', encoding='utf-8')
    assert not webbuild.staleness(tmp_path).stale


def test_staleness_is_stale_after_an_edit(tmp_path: Path) -> None:
    (tmp_path / 'src').mkdir()
    entry = tmp_path / 'src' / 'main.tsx'
    entry.write_text('export {}\n', encoding='utf-8')
    (tmp_path / 'dist').mkdir()
    built = webbuild.source_hash(tmp_path)
    (tmp_path / 'dist' / 'build.json').write_text(f'{{"hash": "{built}"}}', encoding='utf-8')
    entry.write_text('export const changed = true\n', encoding='utf-8')
    state = webbuild.staleness(tmp_path)
    assert state.stale
    assert 'out of date' in state.reason


@pytest.mark.skipif(shutil.which('node') is None, reason='needs Node to compare the two implementations')
def test_the_node_script_agrees_with_python() -> None:
    """The duplication that makes the guard possible, kept honest by a test."""
    result = subprocess.run(
        ['node', str(WEB / 'scripts' / 'source-hash.mjs')],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == webbuild.source_hash()


def test_the_committed_bundle_is_current() -> None:
    """The promise of committing `dist/`: what is in the repository came from these sources."""
    state = webbuild.staleness()
    assert not state.stale, state.message()


def test_the_bundle_can_be_served() -> None:
    """A bundle without an entry point is stale in the only way that is fatal."""
    dist = webbuild.dist_root()
    assert (dist / 'index.html').is_file()
    assert (dist / 'build.json').is_file()
    assert any((dist / 'assets').iterdir())


def test_the_favicon_the_page_declares_is_shipped() -> None:
    """The tab icon is a file the page names, and a build can forget to copy it.

    It comes from `web/public`, so nothing in the bundle references it and no
    import would fail: the only symptom is a blank icon, and the declaration in the
    built page is the contract between the two.
    """
    dist = webbuild.dist_root()
    page = (dist / 'index.html').read_text(encoding='utf-8')
    icon = re.search(r'<link[^>]+rel="icon"[^>]+href="([^"]+)"', page)
    assert icon is not None, 'the built page declares no icon'
    asset = (dist / icon.group(1)).resolve()
    assert dist.resolve() in asset.parents, f'{asset} is outside the bundle'
    assert asset.is_file(), f'{asset} is declared but not shipped'
    assert asset.read_text(encoding='utf-8').lstrip().startswith('<svg')


def test_the_module_entry_point_reports_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m src.server.webbuild` is what CI and the hook's message tell people to run."""
    assert webbuild._main(['--print-hash']) == 0
    assert capsys.readouterr().out.strip() == webbuild.source_hash()
    assert webbuild._main(['--json']) == 0
    assert '"stale": false' in capsys.readouterr().out


def test_the_api_backend_needs_neither_a_password_nor_a_bundle() -> None:
    """`serve` is the only surface that can invent a password or ask for the bundle."""
    from src.server.cli import build_parser, describe
    from src.server.config import PASSWORD_ENV, resolve_config

    parser = build_parser(web=False)
    for flag in (['--password', 'x'], ['--rebuild-ui']):
        with pytest.raises(SystemExit):
            parser.parse_args(flag)

    config = resolve_config(web=False, env={PASSWORD_ENV: 'a-password-for-the-web-client'})

    assert config.web is False
    assert config.password == ''
    assert config.generated is False
    # The configured secret is ignored and never printed: there is no login to use it.
    assert 'a-password-for-the-web-client' not in describe(config)
    assert describe(config).startswith('Sigil backend')
