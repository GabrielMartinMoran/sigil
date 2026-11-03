"""Tests de la lógica pura de la UI (comandos y metadatos del modelo)."""

from src.engine.agents.model_info import context_usage_percent, parse_model_info
from src.engine.commands import match_commands


def test_match_commands_requires_slash():
    assert match_commands('hola') == []


def test_match_commands_filters_by_prefix():
    names = [command.name for command in match_commands('/')]
    assert names == ['/new', '/load', '/help', '/quit']

    assert [c.name for c in match_commands('/n')] == ['/new']
    assert [c.name for c in match_commands('/l')] == ['/load']
    assert match_commands('/zzz') == []


def test_match_commands_ignores_argument():
    assert [c.name for c in match_commands('/load algo')] == ['/load']


def test_parse_model_info_matches_id_and_slug():
    data = [
        {'id': 'other/model', 'name': 'Other', 'canonical_slug': 'other/model', 'context_length': 1000},
        {'id': 'acme/model', 'name': 'Acme', 'canonical_slug': 'acme/model-2024', 'context_length': 200000},
    ]
    info = parse_model_info(data, 'acme/model')
    assert info is not None
    assert info.name == 'Acme'
    assert info.context_length == 200000

    alias = parse_model_info(data, '~acme/model')
    assert alias is not None and alias.context_length == 200000

    assert parse_model_info(data, 'missing/model') is None


def test_parse_model_info_rejects_bad_context_length():
    data = [{'id': 'x/y', 'name': 'X', 'canonical_slug': 'x/y', 'context_length': 0}]
    info = parse_model_info(data, 'x/y')
    assert info is not None and info.context_length is None


def test_context_usage_percent():
    assert context_usage_percent(500, 1000) == 50.0
    assert context_usage_percent(None, 1000) is None
    assert context_usage_percent(500, None) is None
    assert context_usage_percent(2000, 1000) == 200.0
