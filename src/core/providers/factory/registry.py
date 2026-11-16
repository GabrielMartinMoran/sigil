"""Picks the builder for an account and keeps the model-swapping wrapper.

Two responsibilities that belong together because both answer "which model is the
agent using right now":

- ``BuilderRegistry``: an account plus its credential → a ``ModelBuilder``.
- ``ModelRouter``: the ``WrapperModel`` the agent holds, whose wrapped model the
  service swaps between turns. That keeps ``agent.model.context_window`` — which
  the panel, the meter and the debug view read — correct after a switch,
  without rebuilding the agent and losing its tools and capabilities.
"""

from __future__ import annotations
