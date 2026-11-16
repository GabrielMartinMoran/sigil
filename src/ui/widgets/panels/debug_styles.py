"""Debug-only palette and ratio thresholds; change here to tune the display.

Ratios use 0..1, not percentages. Cache reuse is better higher; context
occupancy is riskier higher. Colors supplement labels and numeric values.
"""

CACHE_HIT_WARNING_RATIO = 0.50
CACHE_HIT_GOOD_RATIO = 0.80
CONTEXT_WARNING_RATIO = 0.70
CONTEXT_DANGER_RATIO = 0.90

DYNAMIC_STYLE = '#ffaf5f'
USER_STYLE = 'cyan'
TOOL_STYLE = '#d7afff'
REASONING_STYLE = 'grey62'
AGENT_STYLE = 'white'

PART_STYLES = {
    'dynamic': DYNAMIC_STYLE,
    'user': USER_STYLE,
    'tool_call': TOOL_STYLE,
    'tool_return': TOOL_STYLE,
    'retry': TOOL_STYLE,
    'schema': TOOL_STYLE,
    'thinking': REASONING_STYLE,
    'text': AGENT_STYLE,
    'compaction': AGENT_STYLE,
}


def cache_style(ratio: float | None) -> str:
    if ratio is None:
        return 'dim'
    if ratio >= CACHE_HIT_GOOD_RATIO:
        return 'green'
    return 'yellow' if ratio >= CACHE_HIT_WARNING_RATIO else 'red'


def context_style(ratio: float | None) -> str:
    if ratio is None:
        return 'dim'
    if ratio >= CONTEXT_DANGER_RATIO:
        return 'red'
    return 'yellow' if ratio >= CONTEXT_WARNING_RATIO else 'green'
