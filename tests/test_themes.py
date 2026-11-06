"""Themes as data: loading, validation and the builtin set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.paths import themes_dir
from src.engine.themes import (
    BUILTIN_NAMES,
    ROLES,
    ThemeError,
    ThemeRegistry,
    load_file,
    theme_variable_defaults,
)


def _write(directory, name: str, payload: object) -> None:
    (directory / name).write_text(json.dumps(payload), encoding='utf-8')


def test_the_builtins_are_a_dark_theme_and_synthwave():
    registry = ThemeRegistry.load()
    assert BUILTIN_NAMES == {'dark', 'synthwave'}
    assert registry.names()[:2] == ['dark', 'synthwave']


def test_the_default_theme_is_a_builtin_and_resolves(tmp_path):
    registry = ThemeRegistry.load(tmp_path)
    assert registry.resolve('dark').name == 'dark'
    # A stored preference can name a theme that was uninstalled since.
    assert registry.resolve('gone').name == 'dark'


def test_builtins_define_every_variable_the_stylesheet_uses():
    """The stylesheet names `$tool-border` and friends; every theme must supply them.

    Textual fails to parse the sheet when a variable is undefined, and the failure
    is at startup, on the whole app, for a missing color.
    """
    defaults = theme_variable_defaults()
    assert {'tool-border', 'tool-title', 'tool-output', 'reasoning'} <= set(defaults)
    for name in BUILTIN_NAMES:
        theme = ThemeRegistry.load().get(name)
        assert theme is not None
        assert set(defaults) <= set(theme.colors), f'{name} is missing variables'


def test_the_app_offers_those_defaults_under_textuals_own_name():
    """`get_theme_variable_defaults`, not `theme_variable_defaults`.

    The sheet is parsed while the app is being built, before any Sigil theme is
    registered, and Textual looks for the name with the `get_` prefix. A method
    called anything else is never called, and a `$muted` only Sigil knows fails
    to parse — at startup, on the whole app.
    """
    from src.ui.preferences_mixin import PreferencesMixin

    assert 'muted' in PreferencesMixin.get_theme_variable_defaults(object.__new__(PreferencesMixin))


def test_builtins_define_the_core_roles():
    registry = ThemeRegistry.load()
    for name in BUILTIN_NAMES:
        theme = registry.get(name)
        assert theme is not None
        assert {'primary', 'accent', 'background', 'foreground'} <= set(theme.colors)


def test_a_user_theme_loads_from_a_file(tmp_path):
    _write(tmp_path, 'mine.json', {'name': 'mine', 'dark': True, 'colors': {'primary': '#ff0000'}})
    registry = ThemeRegistry.load(tmp_path)
    theme = registry.get('mine')
    assert theme is not None
    assert theme.colors['primary'] == '#ff0000'
    assert theme.source == tmp_path / 'mine.json'
    assert not theme.builtin


def test_an_installed_theme_appears_after_the_builtins(tmp_path):
    _write(tmp_path, 'aaa.json', {'name': 'aaa', 'colors': {'primary': '#00ff00'}})
    registry = ThemeRegistry.load(tmp_path)
    assert registry.names() == ['dark', 'synthwave', 'aaa']


def test_a_user_file_can_replace_a_builtin(tmp_path):
    """The escape hatch: same layout, your colors."""
    _write(tmp_path, 'dark.json', {'name': 'dark', 'colors': {'primary': '#123456'}})
    registry = ThemeRegistry.load(tmp_path)
    theme = registry.get('dark')
    assert theme is not None
    assert theme.colors['primary'] == '#123456'
    assert not theme.builtin


def test_extra_colors_become_css_variables():
    theme = ThemeRegistry.load().get('dark')
    assert theme is not None
    textual = theme.to_textual()
    assert textual.primary == theme.colors['primary']
    assert textual.variables['tool-border'] == theme.colors['tool-border']
    # A role must not leak into the variables.
    assert 'primary' not in textual.variables


def test_a_broken_file_is_reported_and_skipped(tmp_path):
    (tmp_path / 'bad.json').write_text('{not json', encoding='utf-8')
    _write(tmp_path, 'good.json', {'name': 'good', 'colors': {'primary': '#00ff00'}})
    registry = ThemeRegistry.load(tmp_path)
    assert registry.get('good') is not None
    assert len(registry.errors) == 1
    assert 'bad.json' in registry.errors[0]


def test_a_missing_directory_is_not_an_error(tmp_path):
    registry = ThemeRegistry.load(tmp_path / 'nowhere')
    assert registry.errors == []
    assert registry.names()[:2] == ['dark', 'synthwave']


def test_a_color_that_does_not_parse_rejects_the_whole_file(tmp_path):
    """Half-applying a theme paints an interface nobody designed."""
    _write(
        tmp_path,
        'broken.json',
        {'name': 'broken', 'colors': {'primary': '#ff0000', 'accent': 'not-a-color'}},
    )
    with pytest.raises(ThemeError, match='not-a-color'):
        load_file(tmp_path / 'broken.json')


def test_a_theme_needs_a_name_and_a_primary_color(tmp_path):
    _write(tmp_path, 'noname.json', {'colors': {'primary': '#ff0000'}})
    with pytest.raises(ThemeError, match='name'):
        load_file(tmp_path / 'noname.json')

    _write(tmp_path, 'noprimary.json', {'name': 'x', 'colors': {'accent': '#ff0000'}})
    with pytest.raises(ThemeError, match='primary'):
        load_file(tmp_path / 'noprimary.json')


def test_a_theme_needs_colors(tmp_path):
    _write(tmp_path, 'empty.json', {'name': 'empty', 'colors': {}})
    with pytest.raises(ThemeError, match='no colors'):
        load_file(tmp_path / 'empty.json')


def test_an_unknown_version_is_rejected(tmp_path):
    _write(tmp_path, 'v9.json', {'version': 9, 'name': 'v9', 'colors': {'primary': '#ff0000'}})
    with pytest.raises(ThemeError, match='version'):
        load_file(tmp_path / 'v9.json')


def test_dark_must_be_a_boolean(tmp_path):
    _write(tmp_path, 'x.json', {'name': 'x', 'dark': 'yes', 'colors': {'primary': '#ff0000'}})
    with pytest.raises(ThemeError, match='dark'):
        load_file(tmp_path / 'x.json')


def test_roles_are_the_textual_palette():
    """A role outside this set would silently become a variable instead."""
    assert set(ROLES) == {
        'primary',
        'secondary',
        'accent',
        'warning',
        'error',
        'success',
        'foreground',
        'background',
        'surface',
        'panel',
        'boost',
    }


def test_themes_dir_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('SIGIL_THEMES_DIR', str(tmp_path / 'shared'))
    assert themes_dir() == tmp_path / 'shared'


def test_the_shipped_templates_match_the_builtins():
    """The files under `themes/` are the copy-paste starting point.

    If they drifted from the builtins, the file a user copies would not look like
    the theme they were looking at when they decided to copy it.
    """
    root = Path(__file__).resolve().parent.parent / 'themes'
    assert root.is_dir()
    for theme in ThemeRegistry.load().themes.values():
        if not theme.builtin:
            continue
        path = root / f'{theme.name}.json'
        assert path.is_file(), f'missing template for {theme.name}'
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['name'] == theme.name
        assert data['colors'] == theme.colors
        assert data['dark'] == theme.dark


def test_a_shipped_template_is_a_valid_theme(tmp_path):
    """A template that does not load is worse than no template at all."""
    root = Path(__file__).resolve().parent.parent / 'themes'
    for path in sorted(root.glob('*.json')):
        assert load_file(path).name == path.stem
