"""The LLM summary boundary must not split protected or incomplete exchanges."""

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart

from src.engine.context.compaction.summary import summary_boundary


def test_boundary_preserves_the_five_recent_reasoning_blocks_and_twenty_calls() -> None:
    messages = []
    for index in range(25):
        messages += [
            ModelResponse(parts=[ThinkingPart(f'reason {index}'), ToolCallPart('read', {}, tool_call_id=str(index))]),
            ModelRequest(parts=[ToolReturnPart('read', 'result', tool_call_id=str(index))]),
        ]
    assert summary_boundary(messages) == 10


def test_boundary_never_separates_a_parallel_call_from_its_return() -> None:
    calls = [ToolCallPart('read', {}, tool_call_id=str(i)) for i in range(21)]
    messages = [
        ModelResponse(parts=calls),
        ModelRequest(parts=[ToolReturnPart('read', 'ok', tool_call_id=str(i)) for i in range(21)]),
    ]
    assert summary_boundary(messages) == 0


def test_boundary_never_summarizes_an_incomplete_tool_exchange() -> None:
    messages = [ModelResponse(parts=[ToolCallPart('read', {}, tool_call_id='pending')])]
    messages += [ModelResponse(parts=[TextPart('later')]) for _ in range(25)]
    assert summary_boundary(messages) == 0
