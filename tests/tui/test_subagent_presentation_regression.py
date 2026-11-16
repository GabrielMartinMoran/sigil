import asyncio

from src.ui.widgets.panels.status_bar import StatusBar


def test_status_bar_counts_background_subagents_at_rest() -> None:
    asyncio.run(_check_status_bar())


async def _check_status_bar() -> None:
    bar = StatusBar()
    bar.set_subagents(2)
    assert 'Ready' in bar.plain()
    assert '2 subagents running' in bar.plain()
    bar.set_subagents(1)
    assert '1 subagent running' in bar.plain()
    bar.set_subagents(0)
    assert 'subagent' not in bar.plain()
    bar.on_unmount()
