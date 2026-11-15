from dataclasses import dataclass

from src.core.commands.command_owner import CommandOwner


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    owner: CommandOwner = CommandOwner.CORE
