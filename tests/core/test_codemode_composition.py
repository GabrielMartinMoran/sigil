"""The application composition exposes and wires programmatic file access."""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pytest import MonkeyPatch

from src.core.composition.composition import Composition
from src.core.config.sigil_settings import SigilSettings
from src.core.providers.provider_paths import ProviderPaths


def test_composition_installs_code_with_its_workspace(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'input.txt').write_text('composition marker')
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        'execute_code',
                        {
                            'code': "value = await tools.read_file(path='input.txt')\nprint(value['content'])",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart('done')])

    model = FunctionModel(respond)
    composition = Composition(
        root, settings=SigilSettings(), model=model, provider_paths=ProviderPaths(tmp_path / 'providers')
    )
    try:
        agent = Agent(model)
        composition.install_tools(agent)
        result = asyncio.run(agent.run('Read input through Python.'))
        returns = [
            part for message in result.all_messages() for part in message.parts if isinstance(part, ToolReturnPart)
        ]
        assert len(returns) == 1
        assert 'composition marker' in str(returns[0].content)
        assert 'execute_code' in composition.tool_catalog.names()
    finally:
        composition.close()
