"""The notice helper of the agents layer."""

from __future__ import annotations

PREFIX = 'StepBudget'


def notice(max_steps: int, *, role: str = 'agent') -> str:
    """What the model reads when its budget is gone.

    It says three things, in this order, because each one is useless without the
    next: the budget is over, nothing else can run, and what to produce instead.
    A model that reads "you are out of steps" alone tends to answer with an
    apology; the shape of the answer has to be asked for explicitly.
    """
    return (
        f'{PREFIX}: you have used all {max_steps} steps allowed for this run, and no more '
        'tools will run. Do not ask for another tool call: it cannot be executed.\n'
        f'Answer now with what you have, as the final message of this {role}. Include:\n'
        '- what you established, with the paths or results that support it;\n'
        '- what you did not get to, and what you would do next;\n'
        '- anything the caller needs to continue without you.'
    )
