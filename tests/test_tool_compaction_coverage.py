"""Every own tool declares its compactor, and the view actually reads it."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.engine.bootstrap import Composition, build_strategy
from src.engine.settings import load_settings
from src.engine.tooling.compaction import GENERIC_COMPACTOR
from src.engine.tooling.compaction_policies import ALL_COMPACTORS


def test_the_catalogue_covers_every_registered_tool():
    """A tool without a compactor is a cost nobody decided, and the build refuses it."""
    with tempfile.TemporaryDirectory() as root:
        composition = Composition(Path(root), settings=load_settings({}))
        assert composition.tool_catalog.uncovered() == ()
        declared = set(composition.tool_catalog.names())
        # The tree tools are registered by the factory; every other own tool is here.
        assert {'run', 'read_file', 'load_skill', 'view_image'} <= declared


def test_the_view_reads_the_declared_compactor_and_not_the_generic_one():
    with tempfile.TemporaryDirectory() as root:
        composition = Composition(Path(root), settings=load_settings({}))
        lookup = composition.tool_catalog.compactor
        assert lookup('run') is ALL_COMPACTORS['run']
        assert lookup('summon') is ALL_COMPACTORS['summon']
        assert lookup('an_external_tool') is None


def test_the_strategy_is_built_with_the_catalogue():
    with tempfile.TemporaryDirectory() as root:
        composition = Composition(Path(root), settings=load_settings({}))
        strategy = build_strategy(load_settings({}), lookup=composition.tool_catalog.compactor)
        assert strategy.lookup is not None
        assert strategy.lookup('run') is ALL_COMPACTORS['run']


def test_an_uncovered_tool_is_reported_and_does_not_silently_pass():
    with tempfile.TemporaryDirectory() as root:
        composition = Composition(Path(root), settings=load_settings({}))
        composition.tool_catalog.declare('a_tool_nobody_decided')
        assert composition.tool_catalog.uncovered() == ('a_tool_nobody_decided',)


def test_the_generic_compactor_is_only_the_size_and_never_the_semantics():
    """An external tool has no declaration, so nothing claims to know what it keeps."""
    value = {'rows': ['a' * 9_000]}
    reduced = GENERIC_COMPACTOR.compact_output(value)

    assert reduced['truncated'] is True
    assert len(json.dumps(reduced)) <= 500
