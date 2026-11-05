"""The group table has to match the tools that are actually registered.

A profile is validated against ``BUILTIN_TOOLS``: a scope that is not in it is
rejected as a typo. That check is only worth something while the table matches
reality, and the table is written by hand in a module that the tools never
import. This test closes the loop: it registers every built-in tool the way
``src/sigil.py`` does and reads the declaration back off the toolset the agent
would really advertise.

A tool added without a group fails here, and so does one whose group is written
in two places with two different values.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from src.engine.agents.agent import create_agent
from src.engine.files.tools import register_file_tools
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.tools import register_interaction_tools
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.tools import register_roadmap_tools
from src.engine.shell.tools import register_shell_tools
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools
from src.engine.tooling.permission import (
    BUILTIN_TOOLS,
    GROUP_NAMES,
    PERMISSION_METADATA_KEY,
    ToolGroup,
    permission_of,
)
from src.engine.web.tools import register_web_tools


def _registered_tools() -> dict[str, Any]:
    """The declarations of every built-in tool, read out of the real agent."""
    agent = create_agent(SkillRegistry(), model=TestModel())
    register_shell_tools(agent)
    register_file_tools(agent)
    register_web_tools(agent)
    register_skill_tools(agent, SkillRegistry())
    register_interaction_tools(agent, QuestionHub())
    register_roadmap_tools(agent, RoadmapStore())

    declarations: dict[str, Any] = {}

    async def collect() -> None:
        tools = await agent._function_toolset.get_tools(_context())
        for name, tool in tools.items():
            declarations[name] = tool.tool_def

    asyncio.run(collect())
    return declarations


def _context() -> Any:
    return RunContext(deps=None, model=TestModel(), usage=None)


@pytest.fixture(scope='module')
def declarations() -> dict[str, Any]:
    return _registered_tools()


def test_every_built_in_tool_declares_a_group(declarations):
    missing = sorted(set(declarations) - set(BUILTIN_TOOLS))
    assert not missing, f'tools registered without an entry in BUILTIN_TOOLS: {missing}'


def test_the_table_has_no_entry_for_a_tool_that_does_not_exist(declarations):
    """An MCP server is configured per session, so its meta-tools are the only
    ones this agent does not register here."""
    meta = {
        'list_mcp_servers',
        'list_mcp_resources',
        'read_mcp_resource',
        'list_mcp_prompts',
        'get_mcp_prompt',
    }
    extra = sorted(set(BUILTIN_TOOLS) - set(declarations) - meta)
    assert not extra, f'BUILTIN_TOOLS lists tools nothing registers: {extra}'


def test_the_declared_group_matches_the_table(declarations):
    mismatched = {}
    for name, tool_def in declarations.items():
        declaration = permission_of(tool_def)
        if declaration is None:
            mismatched[name] = 'no declaration'
            continue
        expected = BUILTIN_TOOLS.get(name)
        if expected is None:
            continue
        if ToolGroup(expected) not in declaration.groups:
            mismatched[name] = f'{declaration.groups} does not include {expected}'
    assert not mismatched, mismatched


def test_the_group_names_are_the_groups_the_tools_use(declarations):
    used = {
        str(group)
        for tool_def in declarations.values()
        for group in (permission_of(tool_def).groups if permission_of(tool_def) else ())
    }
    assert used <= set(GROUP_NAMES)


@pytest.mark.parametrize('group', sorted(GROUP_NAMES))
def test_every_group_has_at_least_one_tool(group):
    """A group nobody belongs to is a word in the docs that does nothing."""
    assert group in {str(value) for value in BUILTIN_TOOLS.values()}


def test_the_declaration_travels_as_metadata_and_not_in_the_schema(declarations):
    for name, tool_def in declarations.items():
        assert PERMISSION_METADATA_KEY in (tool_def.metadata or {}), name
        assert PERMISSION_METADATA_KEY not in (tool_def.description or ''), name
