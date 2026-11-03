# Sigil

Agente de terminal construido con [pydantic-ai](https://ai.pydantic.dev/) y
[Textual](https://textual.textualize.io/).

## Agent Skills

Sigil implementa el protocolo [Agent Skills](https://github.com/anthropics/skills):
carpetas autocontenidas con un `SKILL.md` (frontmatter YAML + instrucciones
Markdown) y recursos opcionales (`scripts/`, `references/`, `assets/`).

### Dónde se buscan

En orden de precedencia:

1. `<cwd>/.agents/skills` — skills del proyecto.
2. `~/.agents/skills` — skills globales del usuario.

Un skill del proyecto con el mismo nombre que uno global lo sombrea. También se
puede fijar una lista explícita de raíces con `SIGIL_SKILLS_DIRS` (separadas por
`:`).

### Progressive disclosure

| Nivel | Qué se carga | Cuándo |
| --- | --- | --- |
| 1 | `name` + `description` | Siempre, inyectado en las instrucciones del agente |
| 2 | Cuerpo del `SKILL.md` | Al llamar `load_skill` |
| 3 | Recursos y scripts | Al llamar `read_skill_resource` / `run_skill_script` |

### Tools expuestas al modelo

- `list_skills(query=None)` — lista metadatos de los skills instalados.
- `load_skill(name)` — devuelve el cuerpo del `SKILL.md` y el inventario de recursos.
- `read_skill_resource(name, path, max_bytes=...)` — lee un recurso relativo al
  skill (texto UTF-8 o binario en base64). Bloquea `..` y rutas absolutas.
- `run_skill_script(name, script, args=None, timeout_seconds=30)` — ejecuta un
  script empaquetado con el directorio del skill como `cwd`.

### Logging automático de tools

Las tools se registran con `ToolRegistrar` en vez de `agent.tool_plain` / `agent.tool`.
El wrapper loguea cada llamada (`start`, `success`, `error`, duración y args) y
la UI lo refleja en el chat. Funciona igual para tools sync, async y con
`RunContext`.

```python
from src.engine.tools import ToolLoggerHub, ToolRegistrar

hub = ToolLoggerHub()  # la app le agrega su sink al montar
registrar = ToolRegistrar(agent, hub)


@registrar.tool
def my_tool(query: str) -> str: ...


@registrar.context_tool
def my_ctx_tool(ctx: RunContext[None], query: str) -> str: ...
```

Para tools externas que ya existen como función:

```python
registrar.register(existing_function, name='external-tool')
# o, desde un ToolManager:
tool_manager.register_tool(agent, existing_function)
```

Los scripts se ejecutan con el intérprete de Sigil para `.py`, `bash` para
`.sh`/`.bash`, `node` para `.js`, y shebang/permisos para el resto. Heredan el
guard `SIGIL_SELF_GUARD`, que impide lanzar otra instancia de Sigil.

### Formato del `SKILL.md`

```markdown
---
name: my-skill
description: When to use this skill and what it does.
license: MIT                 # opcional
allowed-tools: read write    # opcional
metadata:                    # opcional
  version: "1.0"
compatibility: ...           # opcional
disable-model-invocation: false  # opcional
---

# My Skill

Instructions...
```

`name` debe ser kebab-case (máx. 64 caracteres) y `description` es el mecanismo
principal de activación (máx. recomendado 1024 caracteres).

## Desarrollo

```bash
uv run ./main.py     # ejecutar Sigil
uv run pytest        # tests
uv run ruff check .  # lint
```
