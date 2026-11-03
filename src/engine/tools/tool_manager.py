import subprocess

import rich
from pydantic.root_model import BaseModel
from pydantic_ai import Agent


class _RunToolOutput(BaseModel):
    returncode: int
    stdout: str | None = None
    stderr: str | None = None


class ToolManager:
    def __init__(self) -> None:
        self.tools = {}

    def register_tools(self, agent: Agent) -> None:

        @agent.tool_plain
        def run(cmd: str) -> _RunToolOutput:
            """Run a bash command and returns the output."""
            rich.print(f'[bold green]Running command:[/bold green] [dodger_blue1]{cmd}[/dodger_blue1]')
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                return _RunToolOutput(returncode=e.returncode, stdout=e.stdout, stderr=e.stderr)
            except Exception as e:
                return _RunToolOutput(returncode=1, stdout=None, stderr=str(e))
            return _RunToolOutput(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
