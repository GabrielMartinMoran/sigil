"""Shared skills test support."""

from pathlib import Path


def write_skill(
    root: Path,
    name: str,
    *,
    description: str = 'A test skill.',
    body: str = '# Body\n\nDo the thing.',
    frontmatter: str = '',
    files: dict[str, str | bytes] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: {description}\n{frontmatter}---\n\n{body}\n',
        encoding='utf-8',
    )
    for relative, content in (files or {}).items():
        target = skill_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding='utf-8')
    return skill_dir


class _Ctx:
    """The minimum of a `RunContext` the skills tools read: their node."""

    deps = None


class DummyAgent:
    def __init__(self) -> None:
        self.registered = {}
        self.options: dict[str, dict[str, object]] = {}

    def tool_plain(self, func, **options):
        self.registered[func.__name__] = func
        self.options[func.__name__] = options
        return func

    def tool(self, func, **options):
        """The context flavour: `load_skill` reads the run to find its node."""
        self.registered[func.__name__] = func
        self.options[func.__name__] = options
        return func
