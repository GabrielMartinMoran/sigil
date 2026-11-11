"""Scrollable, session-scoped controls for configured MCP servers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.mcp.tool_models.server_status import ServerStatus
from src.ui.widgets.provider_modal import ProviderModal


def _row(status: ServerStatus) -> Text:
    text = Text('● ' if status.enabled else '○ ', style='green' if status.enabled else 'dim')
    text.append(status.name, style='bold')
    text.append(f'  {"on" if status.enabled else "off"}  ·  {status.transport}', style='dim')
    if status.enabled and status.error:
        text.append(f'  ·  {status.error}', style='red')
    if status.enabled and status.tools:
        text.append(f'\n  tools: {", ".join(status.tools)}', style='dim')
    return text


class MCPScreen(ProviderModal):
    """Toggle servers without leaving the list or changing global configuration."""

    TITLE_TEXT = 'MCP servers · current session'
    HINT_TEXT = '↑/↓ navigate · Enter toggle · Esc close'
    BINDINGS: ClassVar[list[Binding]] = [Binding('escape', 'close_screen', 'Close', show=False)]

    def __init__(
        self,
        statuses: Sequence[ServerStatus],
        toggle: Callable[[str, bool], Awaitable[ServerStatus]],
        probe: Callable[[], Awaitable[list[ServerStatus]]] | None = None,
    ) -> None:
        super().__init__()
        self._statuses = {status.name: status for status in statuses}
        self._toggle = toggle
        self._probe = probe

    def compose_body(self) -> ComposeResult:
        if not self._statuses:
            yield Static('No MCP servers configured. Add mcp_servers to .sigil/config.json.')
        yield OptionList(*(Option(_row(status), id=status.name) for status in self._statuses.values()), id='mcp-list')

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        if self._probe is not None:
            self.run_worker(self._refresh_statuses())

    async def _refresh_statuses(self) -> None:
        assert self._probe is not None
        try:
            statuses = await self._probe()
        except Exception:  # noqa: BLE001 - diagnostics must not close the modal
            return
        if not self.is_mounted:
            return
        options = self.query_one(OptionList)
        for status in statuses:
            previous = self._statuses.get(status.name)
            if previous is not None and previous.enabled == status.enabled:
                self._statuses[status.name] = status
                options.replace_option_prompt(status.name, _row(status))

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id
        if name is None:
            return
        status = await self._toggle(name, not self._statuses[name].enabled)
        self._statuses[name] = status
        event.option_list.replace_option_prompt(name, _row(status))
        if status.enabled and self._probe is not None:
            self.run_worker(self._refresh_statuses())

    def action_close_screen(self) -> None:
        self.dismiss(None)
