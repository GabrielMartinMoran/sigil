"""All LOD character budgets, ordered (LOD 1, LOD 2, LOD 3).

Generic sizes budget the whole serialized value. Semantic sizes budget named text
fields; identity, user decisions, status, and errors remain protected. Edit values
here and restart Sigil; no environment access or IO occurs in a policy function.
"""

GENERIC_LOD_CHARS: tuple[int, int, int] = (2_000, 500, 100)

SEMANTIC_LOD_CHARS: dict[str, tuple[int, int, int]] = {
    'process_input': (400, 160, 80),
    'process_output': (600, 200, 60),
    'file_edit_input': (400, 120, 0),
    'file_output': (800, 240, 60),
    'search_input': (200, 120, 60),
    'search_snippet': (400, 120, 40),
    'page_output': (600, 200, 60),
    'script_argument': (120, 40, 40),
    'skill_description': (160, 80, 40),
    'loaded_skill_description': (200, 100, 40),
    'resource_note': (100, 100, 100),
    'question_input': (300, 120, 60),
    'question_option': (80, 80, 80),
    'question_result_note': (300, 200, 100),
    'roadmap_input': (120, 60, 30),
    'roadmap_output': (120, 60, 40),
    'image_note': (200, 120, 60),
    'agent_input': (400, 160, 80),
    'agent_output': (400, 160, 80),
    'agent_task': (120, 60, 40),
    'mcp_output': (300, 120, 40),
    'mcp_description': (200, 80, 40),
    'mcp_argument': (80, 80, 80),
}

SCRIPT_ARGUMENT_COUNTS: tuple[int | None, int | None, int | None] = (None, 5, 5)
"""None preserves every script argument at that level; smaller lists are labeled by omission."""
