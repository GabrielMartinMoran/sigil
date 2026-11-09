"""Character budgets for the semantic part of each tool's compacted form.

Semantic budgets bound named text fields; identity, user decisions, status, and errors
remain protected. A tool has one compacted form, so it has one budget. Edit values here
and restart Sigil; no environment access or IO occurs in a compaction function.
"""

SEMANTIC_COMPACT_CHARS: dict[str, int] = {
    'process_input': 400,
    'process_output': 600,
    'file_edit_input': 400,
    'file_output': 800,
    'search_input': 200,
    'search_snippet': 400,
    'page_output': 600,
    'script_argument': 120,
    'skill_description': 160,
    'loaded_skill_description': 200,
    'resource_note': 100,
    'question_input': 300,
    'question_option': 80,
    'question_result_note': 300,
    'roadmap_input': 120,
    'roadmap_output': 120,
    'image_note': 200,
    'agent_input': 400,
    'agent_output': 400,
    'agent_task': 120,
    'mcp_output': 300,
    'mcp_description': 200,
    'mcp_argument': 80,
}
