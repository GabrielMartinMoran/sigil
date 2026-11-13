"""The tree screen when the session has a squad: order, gutter, filter and spinner.

The list has to answer two questions and it used to answer them in one order: what is
running under this conversation, and what happened. With forty-five subagents the
first answer was buried under the second, so what is pinned here is the rule that
fixed it — the branches with work first, history newest first, and a rule between
them — plus the two things that make a big list navigable: a gutter that draws the
ancestry once the tree has depth, and a filter that searches the same rows.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.text import Text
from textual.app import App
from textual.widgets import Input, OptionList

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.store import AgentTreeStore
from src.ui.widgets.agent_tree_screen import (
    BAR,
    BRANCH,
    INDENT,
    LAST,
    AgentTreeScreen,
    Gap,
    arrange,
    empty_row,
    guides,
    history_row,
    matches,
    node_row,
    status_text,
)
from src.ui.widgets.status_bar import SPINNER_FRAMES
from tests.ui_helpers import render_text

# --- a small, deterministic squad --------------------------------------------


def _tree() -> Any:
    """Root, a live developer with two finished children, and one finished branch."""
    tree = AgentTreeStore().active
    root = tree.ensure_root('pulsar_agent_squad')
    lead = tree.add(
        parent_id=root.id, name='pas_developer', role=AgentRole.SUBAGENT, task='close the lock', mode='sync'
    )
    for task in ('qa the lock slice', 'qa the release path'):
        qa = tree.add(parent_id=lead.id, name='pas_qa', role=AgentRole.SUBAGENT, task=task)
        qa.finish('ok')
        qa.finished_at = 400.0
    old = tree.add(parent_id=root.id, name='pas_researcher', role=AgentRole.SUBAGENT, task='read the tree')
    old.finish('ok')
    old.finished_at = 300.0
    return tree


def _plain(option: Any) -> str:
    return render_text(option.prompt)


def _raw(option: Any) -> str:
    """The two lines of a row as painted, without a console wrapping them."""
    return option.prompt.plain


def _by_name(tree: Any, name: str) -> list[Any]:
    return [node for node in tree.nodes() if node.name == name]


# --- the order ---------------------------------------------------------------


def test_the_branch_that_still_holds_work_comes_before_history():
    tree = _tree()
    painted, separator = arrange(tree.nodes())

    assert [node.name for node in painted] == [
        'pulsar_agent_squad',
        'pas_developer',
        'pas_qa',
        'pas_qa',
        'pas_researcher',
    ]
    assert separator == 4, 'history starts right after the live branch'


def test_history_is_newest_first():
    tree = _tree()
    root = tree.root
    older = tree.add(parent_id=root.id, name='pas_qa', role=AgentRole.SUBAGENT, task='old')
    older.finish('ok')
    older.finished_at = 150.0
    newer = tree.add(parent_id=root.id, name='pas_qa', role=AgentRole.SUBAGENT, task='new')
    newer.finish('ok')
    newer.finished_at = 500.0

    painted, separator = arrange(tree.nodes())
    history = [node for node in painted[separator:] if node.status.finished]

    assert history[0].id == newer.id
    assert history[-1].id == older.id


def test_a_live_branch_is_not_split_by_its_finished_children():
    """The developer and the QAs under it travel together, above the rule."""
    tree = _tree()
    painted, separator = arrange(tree.nodes())
    lead = _by_name(tree, 'pas_developer')[0]

    children = [node for node in painted if node.parent_id == lead.id]
    assert len(children) == 2
    assert all(painted.index(child) < separator for child in children)


def test_work_comes_before_history_inside_a_branch_too():
    """The rule repeats down the tree: a running QA is not buried in its branch."""
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    lead = tree.add(parent_id=root.id, name='pas_developer', role=AgentRole.SUBAGENT, task='feature')
    done = tree.add(parent_id=lead.id, name='pas_qa', role=AgentRole.SUBAGENT, task='old pass')
    done.finish('ok')
    running = tree.add(parent_id=lead.id, name='pas_qa', role=AgentRole.SUBAGENT, task='new pass')

    painted, _ = arrange(tree.nodes())

    assert [node.id for node in painted] == [root.id, lead.id, running.id, done.id]


def test_a_flat_tree_has_no_rule():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    for index in range(3):
        node = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task=f't{index}')
        node.finish('ok')

    _painted, separator = arrange(tree.nodes())

    assert separator == -1


# --- the gutter --------------------------------------------------------------


def test_a_flat_tree_keeps_plain_indentation():
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    child = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='go')

    assert guides([root, child]) == ['', '  ']


def test_a_nested_tree_draws_its_ancestry():
    tree = _tree()
    painted, _ = arrange(tree.nodes())

    strips = guides(painted)

    # The live developer closes nothing: its branch goes on below it.
    assert strips[1] == BRANCH
    # The QAs are under it, and its bar continues on their left.
    assert strips[2] == f'{BAR}{BRANCH}'
    assert strips[3] == f'{BAR}{LAST}'


def test_a_third_level_is_painted_under_its_own_parent():
    """Depth is two columns a level, and the bars say whose branch you are on."""
    tree = AgentTreeStore().active
    root = tree.ensure_root('wizard')
    lead = tree.add(parent_id=root.id, name='pas_developer', role=AgentRole.SUBAGENT, task='feature 2')
    sibling = tree.add(parent_id=root.id, name='pas_qa', role=AgentRole.SUBAGENT, task='qa it')
    qa = tree.add(parent_id=lead.id, name='pas_qa', role=AgentRole.SUBAGENT, task='qa the slice')
    deep = tree.add(parent_id=qa.id, name='scout', role=AgentRole.SUBAGENT, task='read the doc')

    painted, _ = arrange(tree.nodes())
    strips = guides(painted)

    assert [node.id for node in painted] == [root.id, lead.id, qa.id, deep.id, sibling.id]
    # `lead` has a sibling below it, so its bar goes on under the grandchild.
    assert strips[1] == BRANCH
    assert strips[2] == f'{BAR}{LAST}'
    assert strips[3] == f'{BAR}{INDENT}{LAST}'
    assert strips[4] == LAST


def test_the_task_line_continues_the_bar_instead_of_restarting_the_branch():
    tree = _tree()
    painted, _ = arrange(tree.nodes())
    strips = guides(painted)
    lead = _by_name(tree, 'pas_developer')[0]

    header, task = _raw(node_row(lead, guide=strips[1])).split('\n')

    assert header.startswith(BRANCH)
    # Under a `├` the line below starts with `│` on the same column, so the gutter
    # reads as one column and not as two branches.
    assert task.startswith(f'{BAR}   ')
    assert 'close the lock' in task


# --- the rows ----------------------------------------------------------------


def test_the_rule_between_work_and_history_cannot_be_chosen():
    option = history_row(42)

    assert option.id is None
    assert option.disabled is True
    assert 'finished · 42' in _plain(option)


def test_the_empty_state_is_a_row_that_says_what_will_fill_it():
    option = empty_row()

    assert option.id is None
    assert option.disabled is True
    assert 'Subagents appear here while the agent works.' in _plain(option)


def test_a_session_without_subagents_opens_on_the_empty_state():
    """A truncated tree is not an error: the screen has a body for it."""
    tree = AgentTreeStore().active
    tree.ensure_root('pulsar_agent_squad')
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> tuple[str, list[str]]:
        status = render_text(screen.query_one('#tree-status').render())
        rows = [render_text(option.prompt) for option in screen.query_one(OptionList).options]
        return status, rows

    status, rows = _run(screen, body)

    assert 'No subagents' in status
    # The trunk, and the line that says what will appear under it.
    assert len(rows) == 2
    assert 'Pulsar Agent Squad' in rows[0]
    assert 'appear here' in rows[1]
    assert screen._rows[-1] is Gap.EMPTY


def test_the_empty_row_is_not_a_stop_for_the_arrows():
    tree = AgentTreeStore().active
    tree.ensure_root('wizard')
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        options = screen.query_one(OptionList)
        options.highlighted = 0

        screen.action_move(1)

        assert options.highlighted == 0, 'there is nowhere else to go'

    _run(screen, body)


def test_a_running_subagent_spins_and_the_root_does_not():
    tree = _tree()
    painted, _ = arrange(tree.nodes())

    spinning = _plain(node_row(painted[1], frame=SPINNER_FRAMES[0]))
    trunk = _plain(node_row(painted[0], frame=SPINNER_FRAMES[0]))

    assert SPINNER_FRAMES[0] in spinning
    assert SPINNER_FRAMES[0] not in trunk
    assert '●' in trunk, 'the root is the trunk, not a job'


def test_the_status_line_counts_what_is_left_and_what_a_filter_left():
    tree = _tree()

    plain = render_text(Text.from_markup(status_text(tree.nodes())))
    filtered = render_text(Text.from_markup(status_text(tree.nodes(), shown=2, query='qa')))

    assert '1 running' in plain
    assert '3 finished' in plain
    assert '2 matching “qa”' in filtered

    solo = AgentTreeStore().active
    root = solo.ensure_root('wizard')
    solo.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='go')
    assert '1 subagent ·' in render_text(Text.from_markup(status_text(solo.nodes()))), 'one subagent, not one subagents'


def test_a_filter_that_matches_nothing_says_so_in_english():
    tree = _tree()

    status = render_text(Text.from_markup(status_text(tree.nodes(), shown=0, query='nonexistent')))

    assert 'No agent matches “nonexistent”' in status
    assert 'Esc clears' in status


# --- filtering ---------------------------------------------------------------


def test_the_filter_matches_the_name_the_id_the_state_and_the_task():
    tree = _tree()
    nodes = tree.nodes()
    qas = _by_name(tree, 'pas_qa')

    by_name = matches(nodes, 'pas_qa')
    by_id = matches(nodes, qas[0].short_id)
    by_state = matches(nodes, 'done')
    by_task = matches(nodes, 'release path')

    assert [node.id for node in by_name] == [tree.root.id, qas[0].id, qas[1].id]
    assert [node.id for node in by_id] == [tree.root.id, qas[0].id]
    assert len(by_state) == 4, 'the root, plus the three that are done'
    assert by_task[1].task == 'qa the release path'


def test_the_root_stays_when_a_filter_hides_everything_else():
    """The trunk is one row and the list needs a top."""
    tree = _tree()

    kept = matches(tree.nodes(), 'nothing-matches-this')

    assert [node.id for node in kept] == [tree.root.id]


# --- the screen --------------------------------------------------------------


def _run(screen: AgentTreeScreen, body: Any) -> Any:
    async def inner() -> Any:
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            return await body(app, pilot)

    return asyncio.run(inner())


def test_the_cursor_opens_on_the_subagent_that_is_working():
    """Landing on the root is landing on the one row that is never the answer."""
    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        highlighted = screen.query_one(OptionList).highlighted_option

        assert highlighted is not None
        assert highlighted.id == _by_name(tree, 'pas_developer')[0].id

    _run(screen, body)


def test_the_cursor_stays_on_the_row_it_was_on():
    tree = _tree()
    viewed = _by_name(tree, 'pas_researcher')[0]
    screen = AgentTreeScreen(tree.nodes(), viewed=viewed.id)

    async def body(app: Any, pilot: Any) -> None:
        assert screen.query_one(OptionList).highlighted_option.id == viewed.id

    _run(screen, body)


def test_the_arrows_step_over_the_rule():
    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        options = screen.query_one(OptionList)
        separator = screen._rows.index(Gap.HISTORY)
        options.highlighted = separator - 1

        screen.action_move(1)

        assert options.highlighted == separator + 1, 'the rule is a label, not a row'

    _run(screen, body)


def test_typing_after_slash_filters_and_escape_gives_the_keys_back():
    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        options = screen.query_one(OptionList)
        field = screen.query_one(Input)
        before = len(options.options)

        await pilot.press('/')
        assert screen.focused is field, '/ hands the keyboard to the filter'

        await pilot.press('r', 'e', 'l', 'e', 'a', 's', 'e')
        assert field.value == 'release'
        assert len(options.options) < before

        await pilot.press('escape')
        assert app.screen is screen, 'the first Esc steps back to the list'
        assert field.value == ''
        assert screen.focused is options

        await pilot.press('escape')
        assert app.screen is not screen, 'the second Esc closes'

    _run(screen, body)


def test_escape_clears_a_filter_from_the_list_before_closing():
    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        field = screen.query_one(Input)
        field.value = 'qa'
        await pilot.pause()

        await pilot.press('escape')
        assert app.screen is screen
        assert field.value == ''

        await pilot.press('escape')
        assert app.screen is not screen

    _run(screen, body)


def test_a_running_row_animates_and_the_timer_stops_with_the_work():
    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())

    async def body(app: Any, pilot: Any) -> None:
        options = screen.query_one(OptionList)
        trunk = _plain(options.get_option_at_index(0))

        assert screen._timer is not None, 'a running subagent animates the list'
        # The timer is live, so the frame to expect is the one the screen is on, read
        # in the same breath: no await between the two, so nothing can tick in between.
        frame = SPINNER_FRAMES[screen._frame]
        assert frame in _plain(options.get_option_at_index(1))

        screen._advance()
        assert SPINNER_FRAMES[screen._frame] in _plain(options.get_option_at_index(1))
        assert SPINNER_FRAMES[screen._frame] != frame
        assert _plain(options.get_option_at_index(0)) == trunk, 'the trunk is still'

        # The work ends: the row settles and the timer goes with it.
        for node in tree.nodes():
            if not node.status.finished:
                node.finish('ok')
        screen.set_nodes(tree.nodes())
        await pilot.pause()
        assert screen._timer is None
        assert not any(frame in _plain(options.get_option_at_index(1)) for frame in SPINNER_FRAMES)

    _run(screen, body)


def test_the_view_speaks_english_from_the_hint_to_the_filter():
    hint = AgentTreeScreen.HINT_TEXT

    async def body(app: Any, pilot: Any) -> tuple[str, str]:
        return (
            screen.query_one(Input).placeholder,
            render_text(screen.query_one('#tree-status').render()),
        )

    tree = _tree()
    screen = AgentTreeScreen(tree.nodes())
    placeholder, status = _run(screen, body)

    assert 'move' in hint and 'open' in hint and 'background' in hint and 'stop' in hint
    assert 'filter' in hint
    assert 'Filter by' in placeholder
    assert 'subagents' in status and 'running' in status


# --- the drawing rules, proven on what the service projects -------------------


def _projection() -> dict[str, Any]:
    """One tree as `/api/state` sends it: JSON, names and numbers, no engine objects.

    A live branch with a grandchild (so the gutter has depth to draw), and one branch
    that already finished (so there is a history section to separate).
    """
    return {
        'root_id': 'a',
        'nodes': [
            {
                'id': 'a',
                'name': 'wizard',
                'role': 'agent',
                'parent_id': None,
                'depth': 0,
                'task': '',
                'status': 'running',
                'mode': 'sync',
                'model': 'test',
                'elapsed': 2.0,
                'runs': 1,
                'delivered': True,
                'budget_exhausted': False,
            },
            {
                'id': 'b',
                'name': 'pas_developer',
                'role': 'subagent',
                'parent_id': 'a',
                'depth': 1,
                'task': 'close the lock',
                'status': 'running',
                'mode': 'sync',
                'model': 'test',
                'elapsed': 3.0,
                'runs': 1,
                'delivered': True,
                'budget_exhausted': False,
            },
            {
                'id': 'c',
                'name': 'pas_qa',
                'role': 'subagent',
                'parent_id': 'b',
                'depth': 2,
                'task': 'qa the lock',
                'status': 'running',
                'mode': 'sync',
                'model': 'test',
                'elapsed': 1.0,
                'runs': 1,
                'delivered': True,
                'budget_exhausted': False,
            },
            {
                'id': 'd',
                'name': 'pas_researcher',
                'role': 'subagent',
                'parent_id': 'a',
                'depth': 1,
                'task': 'read the tree',
                'status': 'done',
                'mode': 'async',
                'model': 'test',
                'elapsed': 4.5,
                'runs': 1,
                'delivered': True,
                'budget_exhausted': False,
                'result': 'looks fine',
            },
        ],
    }


def test_a_projection_becomes_the_rows_the_screen_draws():
    """The service's tree paints the rule, the gutter and the spinner, from its JSON.

    `_nodes_from_tree` is the one place the projection becomes the type this screen
    already knows, so these three rules are proven on its output rather than on a
    hand-built store: what is running sits above the history rule, the gutter draws
    ancestry once the tree has depth, and a running row carries the frame the screen
    is on while the trunk — running for the session's whole life — does not.
    """
    from src.ui.tree_mixin import _nodes_from_tree

    nodes = _nodes_from_tree(_projection())
    screen = AgentTreeScreen(nodes)

    async def body(app: Any, pilot: Any) -> None:
        options = list(screen.query_one(OptionList).options)
        raw = [_raw(option) for option in options]

        # The rule between work and history, and it cannot be picked.
        assert screen._rows[3] is Gap.HISTORY
        assert options[3].disabled is True
        assert 'finished' in raw[3]

        # Depth draws a gutter: one level is a branch, two carry the bar as well.
        assert raw[1].startswith(BRANCH)
        assert raw[2].startswith(f'{BAR}{LAST}')

        # The live rows spin; the trunk does not, though it reports running.
        assert any(frame in raw[1] for frame in SPINNER_FRAMES)
        assert not any(frame in raw[0] for frame in SPINNER_FRAMES)

    _run(screen, body)
