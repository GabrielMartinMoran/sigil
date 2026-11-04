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

## MCP (Model Context Protocol)

Sigil consume servidores [MCP](https://github.com/modelcontextprotocol): las tools
que publican se suman a las del agente, y sus resources y prompts quedan
disponibles como meta-tools. Soporta stdio y HTTP (Streamable HTTP y SSE).

### Dónde se configura

Todo vive en `.sigil/config.json`, con claves en **snake_case**. Se leen dos
archivos y el del proyecto gana:

1. `<cwd>/.sigil/config.json`
2. `~/.sigil/config.json`

`SIGIL_CONFIG=/ruta/config.json` reemplaza a ambos. El merge es por servidor: una
entrada del proyecto con el mismo nombre reemplaza entera a la global.

### Ejemplo mínimo

```json
{
  "mcp_servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

Las tools de ese server aparecen como `github_<tool>` (por ejemplo
`github_create_issue`). Hay un archivo completo en `.sigil/config.example.json`.

### Campos

| Clave | Tipo | Aplica a | Default | Qué hace |
| --- | --- | --- | --- | --- |
| `command` | string | stdio | — | Ejecutable del servidor |
| `args` | string[] | stdio | `[]` | Argumentos del ejecutable |
| `env` | object | stdio | `{}` | Variables de entorno del proceso |
| `cwd` | string | stdio | hereda el cwd de Sigil | Directorio de trabajo del proceso |
| `keep_alive` | bool | stdio | `true` | Mantiene el proceso vivo entre turnos |
| `url` | string | HTTP | — | Endpoint del servidor |
| `headers` | object | HTTP | `{}` | Headers de cada request |
| `transport` | `"http"` \| `"sse"` | HTTP | inferido | Fuerza el transporte |
| `enabled` | bool | ambos | `true` | Apaga el servidor sin borrarlo |
| `prefix` | string | ambos | nombre del server | Prefijo de las tools; `""` lo desactiva |
| `include_instructions` | bool | ambos | `true` | Inyecta las instrucciones del server en el prompt |
| `init_timeout` | number | ambos | `30` | Segundos para conectar y hacer el handshake |
| `read_timeout` | number | ambos | `300` | Segundos de espera por cada mensaje |
| `include_tools` | string[] | ambos | `[]` | Allowlist de tools |
| `exclude_tools` | string[] | ambos | `[]` | Denylist de tools |

Cada servidor declara `command` (stdio) o `url` (HTTP), nunca ambos. Los campos
de un transporte en el otro son error, no se ignoran. `include_tools` y
`exclude_tools` también son mutuamente excluyentes.

### Variables de entorno en el archivo

Usá `${VAR}` para leer una variable y `${VAR:-default}` para darle un fallback.
Funciona en cualquier valor string, incluido `cwd`:

```json
{
  "mcp_servers": {
    "remoto": {
      "url": "https://ejemplo.com/mcp",
      "headers": { "Authorization": "Bearer ${MCP_TOKEN:-}" }
    }
  }
}
```

Una variable sin default y sin valor es un error de configuración, no un string
vacío silencioso.

### `cwd`: dónde trabaja el servidor

`cwd` sólo aplica a servidores stdio. Sin `cwd` el proceso hereda el cwd de
Sigil, que es lo correcto para servidores que operan sobre el proyecto
(filesystem, git).

Una ruta relativa se resuelve contra el **directorio del archivo de config**, no
contra el cwd de Sigil: `"cwd": ".mcp/playwright"` en `.sigil/config.json` apunta
a `.sigil/.mcp/playwright` sin importar desde dónde arranques. Si el directorio no
existe, ese servidor queda marcado como fallido con el path en el mensaje; Sigil
no crea directorios por vos.

Para Playwright, `cwd` no es la perilla de las capturas: eso se controla con
`--output-dir`.

```json
{
  "mcp_servers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--output-dir", "/tmp/sigil-shots"],
      "cwd": "/tmp"
    }
  }
}
```

### Resources y prompts

No son tools, así que se exponen con cuatro meta-tools:

| Tool | Qué devuelve |
| --- | --- |
| `list_mcp_resources(server=None)` | Recursos publicados, con URI y mime type |
| `read_mcp_resource(server, uri)` | Contenido: UTF-8, o base64 si es binario |
| `list_mcp_prompts(server=None)` | Prompts disponibles y sus argumentos |
| `get_mcp_prompt(server, name, arguments=None)` | El prompt ya renderizado |

Más `list_mcp_servers(connect=False)`, que reporta qué está configurado, qué
falló y, con `connect=true`, qué tools expone cada servidor.

Si no hay servidores configurados, estas tools no se registran: no gastan
contexto.

### Comando `/mcp`

`/mcp` en el chat conecta con cada servidor y muestra transporte, estado, tools y
el último error. Los errores de configuración también aparecen como aviso al
arrancar, sin bloquear la sesión.

### Qué pasa si un servidor falla

Un servidor que no conecta no rompe el turno: aporta cero tools, el resto de la
conversación sigue y el error queda visible en `/mcp`. Los errores de parseo se
reportan por servidor, así que una entrada rota no invalida las demás.

### Limitaciones

Sampling, elicitation y roots no se anuncian al servidor. Requieren que el
cliente preste el modelo o la UI a mitad de turno, y eso abre recursión y
preguntas interactivas dentro de una tool. Un servidor que dependa de esas
capacidades va a fallar al pedirlas.

## Desarrollo

```bash
uv run ./main.py     # ejecutar Sigil
uv run pytest        # tests
uv run ruff check .  # lint
```
