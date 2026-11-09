from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from src.engine.skills.loaded import loaded_resources
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.rendering import render_loaded_skills
from src.engine.skills.tools import register_skill_tools
from tests.test_skills import DummyAgent, write_skill


def test_resource_tool_returns_reference_without_text_or_binary(tmp_path):
    write_skill(tmp_path, 'demo', files={'references/doc.md': 'private text', 'assets/data.bin': b'\x00\xff'})
    agent = DummyAgent()
    register_skill_tools(agent, SkillRegistry([('project', tmp_path)]))
    text = agent.registered['read_skill_resource']('demo', 'references/doc.md')
    binary = agent.registered['read_skill_resource']('demo', 'assets/data.bin')
    assert 'private text' not in text.model_dump_json()
    assert 'Loaded skills' in text.model_dump_json()
    assert binary.binary
    assert 'AP8=' not in binary.model_dump_json()
    assert 'not available' in binary.model_dump_json()


def test_successful_repeated_reads_keep_largest_limit_and_failed_reads_do_not_count():
    def call(call_id, limit):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='read_skill_resource',
                    args={'name': 'demo', 'path': 'a', 'max_bytes': limit},
                    tool_call_id=call_id,
                )
            ]
        )

    def result(call_id, outcome='success'):
        return ModelRequest(
            parts=[ToolReturnPart(tool_name='read_skill_resource', content='ok', tool_call_id=call_id, outcome=outcome)]
        )

    messages = [call('1', 100), result('1'), call('2', 10), result('2'), call('3', 200), result('3', 'failed')]
    assert loaded_resources(messages) == (('demo', 'a', 100),)


def test_missing_loaded_skill_is_reported(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    block = render_loaded_skills(registry, ['gone'], [('gone', 'ref.md', 20)])
    assert 'gone' in block
    assert 'unavailable' in block.lower()


def test_partial_resource_rebuilds_after_compaction_and_multiple_sqlite_resumes(tmp_path):
    import asyncio

    from pydantic_ai.messages import TextPart
    from pydantic_ai.models.test import TestModel

    from src.engine.agents.agent import create_agent
    from src.engine.context.capability import ContextViewCapability
    from src.engine.context.meter.context_meter import ContextMeter
    from src.engine.context.strategy.append_only import AppendOnlyStrategy
    from src.engine.sessions.session_store import SessionStore
    from src.engine.tooling.compaction_policies import ALL_COMPACTORS

    write_skill(tmp_path, 'demo', files={'references/doc.md': 'ABCDEFGHIJ'})
    registry = SkillRegistry([('project', tmp_path)])
    history = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name='read_skill_resource',
                    args={'name': 'demo', 'path': 'references/doc.md', 'max_bytes': 5},
                    tool_call_id='resource',
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name='read_skill_resource', content={'note': 'reference'}, tool_call_id='resource')
            ]
        ),
    ]
    history += [ModelResponse(parts=[TextPart(content='step')]) for _ in range(80)]
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(lookup=ALL_COMPACTORS.get), meter=ContextMeter(window=100000)
    )
    agent = create_agent(registry, model=TestModel(), extra_capabilities=[capability])
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    session.messages = history
    for _ in range(2):
        session.messages = asyncio.run(agent.run('continue', message_history=session.messages)).all_messages()
        store.save(session)
        session = store.load(session.id)
        assert loaded_resources(session.messages) == (('demo', 'references/doc.md', 5),)
        block = render_loaded_skills(registry, [], loaded_resources(session.messages))
        assert 'ABCDE' in block and 'ABCDEFGHIJ' not in block
        assert '(partial)' in block
    target = tmp_path / 'demo' / 'references' / 'doc.md'
    target.write_text('XYZ updated')
    assert 'XYZ u' in render_loaded_skills(registry, [], loaded_resources(session.messages))
    target.unlink()
    assert '(unavailable)' in render_loaded_skills(registry, [], loaded_resources(session.messages))


def test_effective_resource_limit_is_clamped_and_rollback_does_not_resurrect_reads():
    from src.engine.skills.discovery import MAX_READ_BYTES

    call = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name='read_skill_resource',
                args={'name': 'demo', 'path': 'doc', 'max_bytes': MAX_READ_BYTES * 2},
                tool_call_id='r',
            )
        ]
    )
    success = ModelRequest(parts=[ToolReturnPart(tool_name='read_skill_resource', content='ok', tool_call_id='r')])
    assert loaded_resources([call, success]) == (('demo', 'doc', MAX_READ_BYTES),)
    assert loaded_resources([call]) == ()
    assert loaded_resources([]) == ()
