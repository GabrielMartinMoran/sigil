import rich
from pydantic_ai.messages import ModelResponse

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
        for formatted_message in [
            f'[italic bright_black]{x.thinking}[/italic bright_black]\n\n[orange3]{x.text}[/orange3]\n\n'
            for x in result.new_messages()
            if isinstance(x, ModelResponse)
        ]:
            rich.print(formatted_message)
