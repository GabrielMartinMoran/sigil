from typing import Literal

from pydantic import BaseModel


class Message(BaseModel):
    role: Literal['user', 'agent']
    content: str
