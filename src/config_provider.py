import os

from dotenv import load_dotenv


class ConfigProvider:
    load_dotenv()

    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'openrouter')
    LLM_MODEL = os.environ['LLM_MODEL']
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', None)
    OPENCODE_GO_API_KEY = os.environ.get('OPENCODE_GO_API_KEY', None)

    # Hard step limit per turn: every model response and every call to
    # a tool count as one step. Exceeding it aborts the turn.
    MAX_STEPS = int(os.environ.get('SIGIL_MAX_STEPS', '256'))

    # Total attempts per model request (the first one included). Transient
    # provider failures (5xx, 429, dropped connection) are retried with
    # backoff; once the budget is exhausted the original error propagates.
    MODEL_MAX_ATTEMPTS = max(1, int(os.environ.get('SIGIL_MODEL_MAX_ATTEMPTS', '5')))

    # Reasoning effort (model variant): low/medium/high/max.
    # Empty = use the model default.
    REASONING_EFFORT = os.environ.get('SIGIL_REASONING_EFFORT', '').strip().lower()

    # --- context management ------------------------------------------------

    # View strategy: `windowed` trims reasoning, old tool results and old
    # turns; `passthrough` sends the history as is.
    CONTEXT_STRATEGY = os.environ.get('SIGIL_CONTEXT_STRATEGY', 'windowed').strip().lower()

    # Per-provider escape hatch: if a route requires round-tripping the
    # reasoning, you turn it on and the view drops no ThinkingPart.
    CONTEXT_KEEP_THINKING = os.environ.get('SIGIL_CONTEXT_KEEP_THINKING', '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }

    # Turns kept intact before starting to trim.
    CONTEXT_KEEP_RECENT_TURNS = max(1, int(os.environ.get('SIGIL_CONTEXT_KEEP_RECENT_TURNS', '3')))

    # Cap of an old tool result, in characters.
    CONTEXT_MAX_TOOL_RESULT_CHARS = max(200, int(os.environ.get('SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS', '4000')))

    # Tokens reserved for the model response.
    CONTEXT_OUTPUT_RESERVE = max(0, int(os.environ.get('SIGIL_CONTEXT_OUTPUT_RESERVE', '8000')))
