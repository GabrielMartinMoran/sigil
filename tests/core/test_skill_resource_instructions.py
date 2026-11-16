import asyncio
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel

from src.core.agents.agent import create_agent
from src.core.skills.skill_registry import SkillRegistry
from tests.support.skills import write_skill


def test_successful_resource_read_is_injected_into_next_request_without_load_call(tmp_path: Path) -> None:
    write_skill(tmp_path, 'demo', files={'references/doc.md': 'resource detail'})
    agent = create_agent(SkillRegistry([('project', tmp_path)]))
    history = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='read_skill_resource',
                    args={'name': 'demo', 'path': 'references/doc.md'},
                    tool_call_id='r',
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name='read_skill_resource', content={'note': 'see instructions'}, tool_call_id='r')
            ]
        ),
    ]
    with agent.override(model=TestModel()):
        result = asyncio.run(agent.run('continue', message_history=history))
    requests = [m for m in result.all_messages() if isinstance(m, ModelRequest)]
    assert 'resource detail' in requests[-1].instructions
    assert '# Body' not in requests[-1].instructions
    assert 'resource detail' not in str(history[1].parts)
