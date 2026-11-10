from types import SimpleNamespace
from xml.etree import ElementTree

from src.engine.context.meter.request import snapshot_request
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.rendering import render_available_skills, render_loaded_skills
from tests.test_skills import write_skill


def test_skill_sections_escape_content_and_preserve_resource_states(tmp_path):
    write_skill(tmp_path, 'demo', body='Use <example> & details.', files={'references/doc.md': 'A <tag> & B'})
    registry = SkillRegistry([('project', tmp_path)])
    available = ElementTree.fromstring(render_available_skills(registry))
    assert available.find('./skill/description') is not None
    text = render_loaded_skills(registry, ['demo'], [('demo', 'references/doc.md', 5), ('demo', 'missing', 5)])
    root = ElementTree.fromstring(text)
    assert 'Use <example> & details.' in root.find('./skill/skill_instructions').text
    resources = root.findall('./skill/skill_resource')
    assert [r.get('status') for r in resources] == ['partial', 'unavailable']
    assert resources[0].text.strip() == 'A <ta'
    metrics = snapshot_request(
        SimpleNamespace(instruction_parts=[SimpleNamespace(name='loaded-skills', content=text)])
    ).skills
    assert metrics['resources'] == 2 and metrics['partial'] == 1 and metrics['unavailable'] == 1
    assert metrics['resources_tokens'] > 0


def test_skill_catalog_precedes_project_and_loaded_skills(tmp_path):
    from src.engine.agents.agent import create_agent
    from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.engine.context.scope import ScopeTracker

    agent = create_agent(
        SkillRegistry([('project', tmp_path)]), workspace=WorkspaceInstructions(tmp_path), scope=ScopeTracker(tmp_path)
    )
    names = [getattr(item, 'name', None) for item in agent._instructions]
    assert names.index('skills') < names.index('workspace') < names.index('loaded-skills')
