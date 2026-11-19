"""The goal travels in the session projection both sidebars paint from.

The terminal and the web client read `/api/state.session.goal`, and the state is
built from `views.session_view`. This pins that shape at the projection, which is
the one place it is defined, instead of at either renderer.
"""

from src.core.goal.state import Goal
from src.core.sessions.session import Session
from src.server.api import views


def test_a_session_without_a_goal_projects_none() -> None:
    """``None`` is the assertion that no goal was set, and it travels as one."""
    assert views.session_view(Session(id='s1', cwd='/tmp'))['goal'] is None


def test_the_session_projection_carries_the_goal() -> None:
    session = Session(id='s1', cwd='/tmp')
    session.goal = Goal(text='Ship the CSV export', yolo=True, status='active')

    assert views.session_view(session)['goal'] == {
        'text': 'Ship the CSV export',
        'yolo': True,
        'status': 'active',
    }


def test_a_completed_goal_keeps_its_status_in_the_projection() -> None:
    """The client distinguishes completion from the status the server sends."""
    session = Session(id='s1', cwd='/tmp')
    session.goal = Goal(text='Ship the CSV export', status='completed')

    assert views.session_view(session)['goal'] == {
        'text': 'Ship the CSV export',
        'yolo': False,
        'status': 'completed',
    }
