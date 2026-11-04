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

| Nivel | Qué se carga | Dónde viaja |
| --- | --- | --- |
| 1 | `name` + `description` | Bloque de instrucciones `skills`, recalculado por request |
| 2 | Cuerpo del `SKILL.md` | Bloque de instrucciones `loaded-skills`, al llamar `load_skill` |
| 3 | Recursos y scripts | Resultado de `read_skill_resource` / `run_skill_script` |

El nivel 2 va como **bloque de instrucciones**, no como resultado de tool. Así
sobrevive a la compactación y no compite por el presupuesto del historial. Los
recursos quedan afuera a propósito: se leen on demand, que es lo que mantiene
barato el bloque.

### Tools expuestas al modelo

- `list_skills(query=None)` — lista metadatos de los skills instalados.
- `load_skill(name)` — registra el skill y devuelve un acuse con el inventario de
  recursos. El cuerpo del `SKILL.md` aparece en las instrucciones del request
  siguiente, bajo "Loaded skills".
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
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.registrar import ToolRegistrar

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
```

### Concurrencia entre tools

pydantic-ai ejecuta las llamadas hermanas de una misma respuesta del modelo como
tareas concurrentes. Para casi todas las tools eso está bien. Para las que mutan
un recurso compartido, dos llamadas simultáneas se pisan: cada una lee el estado
original y la última en escribir borra el trabajo de la anterior.

Una guarda declara, por tool, cuándo la llamada no puede convivir con las
anteriores de la misma respuesta. Recibe un `ConcurrencyContext` con los
argumentos efectivos y las llamadas anteriores, en orden de emisión, y devuelve
el motivo del conflicto o `None`.

```python
from src.engine.tooling.concurrency import ConcurrencyContext


def no_same_key(ctx: ConcurrencyContext) -> str | None:
    for sibling in ctx.siblings:
        if sibling.tool_name == ctx.tool_name and sibling.args.get('key') == ctx.args.get('key'):
            return f'{ctx.tool_name} already ran for key {ctx.args["key"]!r}'
    return None


@registrar.tool(concurrency=no_same_key)
def my_tool(key: str) -> str: ...
```

La guarda se evalúa en orden de invocación: la primera llamada de un grupo en
conflicto siempre pasa y las siguientes fallan con un `ToolConflict`. El modelo
lo ve como resultado fallido de la tool y puede reemitir la llamada en un paso
aparte. El resto de las llamadas sigue en paralelo, sin barreras globales.

Una llamada directa desde código —sin `RunContext`— se ejecuta igual, sin
guarda: no hay hermanos con los que comparar.

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

## Tools integradas

Además de las skills y de los servidores MCP, Sigil registra un conjunto de
tools propias. Todas pasan por el mismo logger, así que cada llamada aparece en
el chat con su duración y sus argumentos.

| Tool | Qué hace |
| --- | --- |
| `run` | Ejecuta un comando de shell con timeout y guard anti-autoarranque |
| `read_file` | Lee un archivo, o un rango de líneas, con límite de bytes |
| `write_file` | Crea un archivo, o reemplaza o agrega su contenido |
| `patch_file` | Edita un archivo por texto exacto o por rango de líneas |
| `web_search` | Busca en la web y devuelve título, URL y snippet de cada resultado |
| `read_url` | Extrae una página como Markdown, texto o HTML |
| `ask_user` | Pregunta al usuario con opciones, dentro de la UI |
| `update_roadmap` | Publica y actualiza el roadmap de la tarea actual |

Un error de una tool no corta el turno: vuelve al modelo como resultado fallido
(`outcome: failed`) y el agente decide cómo seguir. Si el `old_string` de un
`patch_file` no aparece, el modelo lee el error y corrige la llamada en vez de
devolverte el control a mitad de la tarea.

### Archivos

`read_file`, `write_file` y `patch_file` trabajan dentro del workspace: una ruta
fuera de él se rechaza con error, no se resuelve en silencio.

`patch_file` tiene dos modos:

- **Texto exacto** (`old_string` + `new_string`): el modo recomendado. El texto
  tiene que aparecer una sola vez, salvo `replace_all=true`. Si aparece varias
  veces, el error lista las líneas donde está.
- **Rango de líneas** (`start_line` + `end_line`): útil cuando ya conocés los
  números de línea. Podés validar el contenido actual con `expected_text`, el
  texto que esperás encontrar en ese rango; el salto de línea final se ignora.

Ambos modos respetan los finales de línea del archivo: un archivo con CRLF sigue
con CRLF después del parche.

`write_file` y `patch_file` declaran una guarda de concurrencia: dos llamadas
que escriben el mismo archivo en una misma respuesta chocan. La primera pasa y
la segunda falla con un mensaje que le pide al modelo reemitirla en un paso
aparte. Editar archivos distintos sigue en paralelo, y leer un archivo nunca
choca con editarlo.

### Web

`web_search` usa [ddgs](https://pypi.org/project/ddgs/) y no necesita API key;
devuelve hasta 20 resultados por consulta. `read_url` convierte la página a
Markdown por defecto (`text_markdown`) y corta el resultado en 40 000
caracteres. Los binarios vuelven en base64.

### Preguntas interactivas

`ask_user` abre un modal con 2 o 3 opciones por pregunta —la recomendada aparece
marcada— y un campo libre para escribir otra respuesta. El agente la usa cuando
los requisitos son ambiguos o una acción necesita autorización explícita. Si
cancelás, la tool devuelve `cancelled: true` y el turno sigue sin respuesta.

### Roadmap

`update_roadmap` mantiene la lista de tareas del panel izquierdo. Cada ítem
queda en `pending`, `in_progress`, `done` o `blocked`.

El panel muestra una sola lista en orden de plan, sin agrupar por estado. El
icono y el color alcanzan para leerla de un vistazo:

| Estado | Icono | Estilo |
| --- | --- | --- |
| `done` | `✓` | verde, tachado |
| `in_progress` | `▶` | azul |
| `pending` | `○` | blanco |
| `blocked` | `!` | amarillo |

El roadmap es de la sesión, no del directorio: cada sesión arranca con la lista
vacía, avanza con ella y la recupera al cargarla con `/load`. El estado se
guarda en `~/.sigil/roadmaps/<hash-del-cwd>/<id-de-sesión>.json`. Si la
escritura falla, la lista sigue funcionando en memoria y el error queda en
`persistence_error`.

## Comandos

Los comandos se escriben en el prompt y empiezan con `/`. El menú los filtra
mientras escribís.

| Comando | Qué hace |
| --- | --- |
| `/new` | Inicia una sesión nueva, con su roadmap vacío |
| `/load` | Carga una sesión guardada y su roadmap |
| `/sidebar [on\|off]` | Muestra u oculta el sidebar; sin argumento, alterna |
| `/mcp` | Consulta el estado de los servidores MCP |
| `/context` | Muestra el desglose de contexto usado |
| `/help` | Lista los comandos disponibles |
| `/quit` | Sale de Sigil |

El sidebar arranca visible. Ocultarlo no borra el roadmap: el agente lo sigue
actualizando y vuelve a aparecer con `/sidebar on`.

### Sidebar

Arriba del roadmap, el panel de sesión muestra:

- **Loaded skills**: los skills que el agente cargó en esta sesión, en orden de
  carga. Es el diagnóstico rápido de si está siguiendo el protocolo.
- **MCP**: el conteo va inline con el título, entre paréntesis. Abajo se lista
  cada servidor: `●` en verde para los que andan, `✗` en rojo para los que no.
  Si no hay servidores configurados, la sección no aparece.

Toda la interfaz está en inglés; el código y la documentación, en español.

```
MCP (3, 1 ✗)
 ● github
 ● playwright
 ● filesystem
 ✗ broken
```

El conteo de MCP se refresca al arrancar (en un worker, sin bloquear la UI) y
cada vez que corrés `/mcp`.

## Gestión de contexto

El historial completo de la sesión (el **log**) es append-only y se persiste tal
cual. Lo que se le manda al modelo en cada request es una **vista** derivada: una
proyección determinística que no muta el log. Así se puede recortar sin perder
nada de lo que `/load` restaura.

### Instrucciones por árbol de directorios

`AGENTS.md` (o `CLAUDE.md` en su defecto) se resuelve sobre el árbol de
directorios activo:

- El `AGENTS.md` de la raíz de la sesión aplica siempre.
- El de un subdirectorio aplica sólo cuando el agente opera dentro de él.
- El orden es padre → hijo: el hijo va último y tiene precedencia.
- En un mismo directorio, `AGENTS.md` gana sobre `CLAUDE.md`.

El bloque viaja por el canal de instrucciones (contenido de operador), nunca como
mensaje de usuario, y usa `on_change='append'`: cuando el scope se amplía a mitad
de turno se agrega el texto del hijo en vez de reescribir el prefijo, que es lo
que rompería el prompt cache.

### Estrategias de vista

| Estrategia | Qué hace |
| --- | --- |
| `windowed` (default) | Dropea el razonamiento, trunca tool results viejos y descarta turnos viejos si no entra en la ventana. |
| `compacting` | Además colapsa los turnos viejos en un resumen determinístico (`CompactionPart`), sin llamar al modelo. |
| `passthrough` | Manda el historial tal cual. |

Los turnos recientes quedan intactos: el modelo suele necesitar los detalles
exactos del último turno en el siguiente.

### Medición

La barra de estado muestra el **proyectado**: el medido del último request más
una estimación de lo agregado desde entonces. El medido describe el pasado; el
proyectado es lo que importa para saber si el próximo request entra en la
ventana. `/context` muestra los dos, el presupuesto y el desglose por turno.

### Configuración

| Variable | Default | Qué hace |
| --- | --- | --- |
| `SIGIL_CONTEXT_STRATEGY` | `windowed` | `windowed`, `compacting` o `passthrough`. |
| `SIGIL_CONTEXT_KEEP_THINKING` | vacío | Si se prende, la vista no dropea razonamiento. |
| `SIGIL_CONTEXT_KEEP_RECENT_TURNS` | `3` | Turnos intactos antes de recortar. |
| `SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS` | `4000` | Tope de un tool result viejo. |
| `SIGIL_CONTEXT_OUTPUT_RESERVE` | `8000` | Tokens reservados para la respuesta. |

El diseño completo y las verificaciones contra el provider están en
[`proposals/context-management.md`](proposals/context-management.md).

## Desarrollo

```bash
uv run ./main.py     # ejecutar Sigil
uv run pytest        # tests
uv run ruff check .  # lint
```
