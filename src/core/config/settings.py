"""Runtime settings of the application.

These are the knobs that tune a session (step limits, retries, context strategy). They
are read from the environment **once**, at startup, by the composition root, and then
passed inward as an immutable value object.

The difference with the previous ``ConfigProvider`` matters: that class read
``os.environ`` in its class body, so importing any module that touched it froze the
values and pulled the repository ``.env`` into the process. A frozen dataclass built by
an explicit loader can be constructed in a test with no environment at all.

Provider credentials are *not* here. They belong to the provider accounts the user
configures from the UI (``src/core/providers``).
"""

from __future__ import annotations

"""Model calls per run before the agent is asked for a last, tool-less answer.

It is not an abort: the steps are taken away and the model gets one more call to report
what it established, so a long turn ends with a summary instead of dying. A profile can
declare its own budget with `max_steps` in its frontmatter.
"""

"""Total attempts per model request, the first one included."""

"""`append-only` compacts under pressure; `passthrough` sends the log as it is."""
