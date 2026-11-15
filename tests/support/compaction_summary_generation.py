"""Shared compaction summary generation test support."""

from pydantic_ai.messages import ModelResponse


class FakeModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.messages = []

    async def request(self, messages, model_settings, model_request_parameters):
        self.messages = messages
        assert not model_request_parameters.function_tools
        return self.response
