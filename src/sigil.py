import rich
from pydantic_ai.messages import ModelResponse
from rich.markdown import Markdown

from src.engine.agents.agent import create_agent
from src.engine.tools.tool_manager import ToolManager


def run() -> None:

    agent = create_agent()

    tool_manager = ToolManager()

    tool_manager.register_tools(agent)

    rich.print('[bold magenta]Welcome to Sigil![/bold magenta]')

    while True:
        user_input = input('> ')
        result = agent.run_sync(user_input)

        formatted_messages = []

        for x in result.new_messages():
            if not isinstance(x, ModelResponse):
                continue
            thinking = f'\n\n[italic bright_black]{x.thinking}[/italic bright_black]\n' if x.thinking else None
            output = Markdown(x.text) if x.text else None

            if thinking:
                formatted_messages.append(thinking)
            if output:
                formatted_messages.append(output)
                formatted_messages.append('\n')

        for formatted_message in formatted_messages:
            rich.print(formatted_message)
