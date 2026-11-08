"""Context maintenance tuning, in steps, characters, and window percentages.

Edit these defaults and restart Sigil. Runtime settings can override reasoning
and image targets; policies can override any ceiling for tests or experiments.
These values are provisional calibration inputs, not measured quality guarantees.
"""

REASONING_TARGET_STEPS = 10
REASONING_CEILING_STEPS = 20
REASONING_TARGET_PERCENT = 5
REASONING_CEILING_PERCENT = 10

# When a runtime setting overrides a target, derive its ceiling using this ratio.
# Default targets use their explicit ceilings above instead.
REASONING_CEILING_MULTIPLIER = 2

TOOL_LOD_AGES_STEPS: tuple[int, int, int] = (25, 50, 75)
"""Eligibility ages for LOD 1, 2, and 3; events apply eligible changes together."""

TOOL_PENDING_REDUCTION_CEILING_CHARS = 4_000
TOOL_PENDING_REDUCTION_TARGET_CHARS = 0
"""Pending eligible savings: trigger at ceiling, apply eligible reductions to target."""

SUMMARY_RECENT_TURNS = 2
SUMMARY_TURN_CHARS = 400

# Historical baseline used only by the simulator, never runtime maintenance.
LEGACY_REASONING_STEPS = 10
LEGACY_REASONING_PERCENT = 5
LEGACY_RECENT_TURNS = 3
LEGACY_TOOL_RESULT_MAX_CHARS = 4_000
LEGACY_TOOL_RESULT_HEAD_CHARS = 2_000
