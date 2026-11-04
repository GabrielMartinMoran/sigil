"""Decorador propio para registrar tools con logging automático.

Envolver el registro de tools (``agent.tool_plain`` / ``agent.tool``) permite
loguear cada llamada —argumentos, duración, resultado o error— sin que cada
tool tenga que imprimir nada a mano. El logger es inyectable: el engine no
conoce la UI, y la UI enchufa un sink que escribe en el ``ChatLog``.

Uso típico::

    registrar = ToolRegistrar(agent, logger)

    @registrar.tool
    def my_tool(query: str) -> str:
        ...

    @registrar.context_tool
    def my_ctx_tool(ctx: RunContext[None], query: str) -> str:
        ...

También se puede registrar una tool ya definida con
``registrar.register(func)``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_type_hints

from pydantic_ai import RunContext

from src.engine.tools.concurrency import (
    ConcurrencyContext,
    ConcurrencyGuard,
    ToolConflict,
    earlier_siblings,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent

ToolPhase = Literal['start', 'success', 'error']


def preview(value: Any, limit: int = 200) -> str:
    """Resumen de una línea, seguro para cualquier valor."""
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - el logging nunca debe romper una tool
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """Evento emitido por el wrapper de una tool."""

    tool_name: str
    phase: ToolPhase
    args: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    result_preview: str | None = None
    error: str | None = None


# Un sink puede ser sync o async; el retorno se ignora.
ToolLogger = Callable[[ToolCallEvent], Any]


async def _emit(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    if logger is None:
        return
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001, S110 - el logging nunca debe romper una tool
        pass


async def emit_tool_event(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emite un evento a un sink, ignorando cualquier fallo del logging.

    Público para integraciones que no pasan por ``wrap_tool`` — por ejemplo las
    tools de un servidor MCP, que se ejecutan dentro de un toolset.
    """
    await _emit(logger, event)


def _bind_args(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    drop_first: bool,
) -> dict[str, Any]:
    """Argumentos efectivos de la llamada (con defaults aplicados)."""
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
    except TypeError, ValueError:
        values = dict(kwargs)

    if drop_first and values:
        values.pop(next(iter(values)), None)
    return values


def _prepare_wrapper(wrapper: Callable[..., Any], func: Callable[..., Any], *, prepend_ctx: bool = False) -> None:
    """Preserva nombre/docstring/firma y resuelve anotaciones string.

    ``functools.wraps`` copia ``__annotations__``, pero si el módulo original
    usa ``from __future__ import annotations`` quedan como strings y pydantic-ai
    las resolvería contra los globals del wrapper. Las evaluamos acá y dejamos
    tipos reales en ``__annotations__`` y ``__signature__``.

    Con ``prepend_ctx`` la firma expuesta arranca con un ``RunContext``: es lo
    que necesita una tool con guarda de concurrencia, que recibe el contexto
    aunque su función no lo pida. pydantic-ai lo saca del schema porque la tool
    se registra como context tool.
    """
    functools.update_wrapper(wrapper, func)
    try:
        annotations = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 - fallback a las anotaciones originales
        annotations = dict(getattr(func, '__annotations__', {}))
    try:
        signature: inspect.Signature | None = inspect.signature(func, eval_str=True)
    except Exception:  # noqa: BLE001
        signature = None

    if prepend_ctx and signature is not None:
        ctx_param = inspect.Parameter('ctx', inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=RunContext[Any])
        signature = signature.replace(parameters=[ctx_param, *signature.parameters.values()])
        annotations = {'ctx': RunContext[Any], **annotations}

    wrapper.__annotations__ = annotations
    if signature is not None:
        wrapper.__signature__ = signature  # type: ignore[attr-defined]


def _emit_sync(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emite desde una tool sync; programa sinks async en el loop actual."""
    if logger is None:
        return
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)
    except Exception:  # noqa: BLE001, S110 - el logging nunca debe romper una tool
        pass


def _start_event(
    tool_name: str, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any], *, drop_first: bool
) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name,
        phase='start',
        args=_bind_args(func, args, kwargs, drop_first=drop_first),
    )


def _end_event(tool_name: str, started: float, result: Any) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name,
        phase='success',
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        result_preview=preview(result),
    )


def _error_event(tool_name: str, started: float, exc: BaseException) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name,
        phase='error',
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        error=f'{type(exc).__name__}: {exc}',
    )


def _check_concurrency(
    guard: ConcurrencyGuard,
    *,
    tool_name: str,
    func: Callable[..., Any],
    ctx: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    drop_first: bool,
) -> None:
    """Evalúa la guarda y lanza ``ToolConflict`` si la llamada no puede correr."""
    call_args = _bind_args(func, args, kwargs, drop_first=drop_first)
    tool_call_id = getattr(ctx, 'tool_call_id', None)
    reason = guard(
        ConcurrencyContext(
            tool_name=tool_name,
            args=call_args,
            tool_call_id=tool_call_id,
            siblings=earlier_siblings(
                getattr(ctx, 'messages', ()),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=call_args,
            ),
        )
    )
    if reason:
        raise ToolConflict(reason)


def wrap_tool(
    func: Callable[..., Any],
    *,
    tool_name: str,
    logger: ToolLogger | None = None,
    takes_ctx: bool = False,
    concurrency: ConcurrencyGuard | None = None,
) -> Callable[..., Any]:
    """Envuelve ``func`` para loguear inicio, éxito y error.

    Conserva la naturaleza sync/async de ``func`` para no cambiar cómo la
    ejecuta pydantic-ai ni romper llamadas directas desde otros módulos.

    ``takes_ctx`` indica que ``func`` recibe un ``RunContext`` como primer
    argumento. Con ``concurrency`` el wrapper recibe el contexto aunque ``func``
    no lo pida, para poder evaluar la guarda antes de ejecutar.
    """
    wrapper_takes_ctx = takes_ctx or concurrency is not None
    prepend_ctx = concurrency is not None and not takes_ctx

    def split_ctx(args: tuple[Any, ...]) -> tuple[Any, tuple[Any, ...]]:
        """Separa el ``RunContext`` del wrapper de los argumentos de ``func``.

        Una llamada directa desde código —tests, otros módulos— no trae
        contexto. En ese caso se ejecuta igual, sin guarda: la guarda sólo tiene
        sentido dentro de un run, donde hay hermanos con los que comparar.
        """
        if wrapper_takes_ctx and args and isinstance(args[0], RunContext):
            return args[0], args[1:]
        return None, args

    def forward(ctx: Any, func_args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Argumentos con los que se invoca ``func``."""
        return (ctx, *func_args) if takes_ctx and ctx is not None else func_args

    def guard(ctx: Any, forwarded: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if concurrency is None or ctx is None:
            return
        _check_concurrency(
            concurrency,
            tool_name=tool_name,
            func=func,
            ctx=ctx,
            args=forwarded,
            kwargs=kwargs,
            drop_first=takes_ctx,
        )

    if inspect.iscoroutinefunction(func):

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx, func_args = split_ctx(args)
            forwarded = forward(ctx, func_args)
            await _emit(logger, _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx))
            started = time.perf_counter()
            try:
                guard(ctx, forwarded, kwargs)
                result = await func(*forwarded, **kwargs)
            except Exception as exc:
                await _emit(logger, _error_event(tool_name, started, exc))
                raise
            await _emit(logger, _end_event(tool_name, started, result))
            return result

        _prepare_wrapper(async_wrapper, func, prepend_ctx=prepend_ctx)
        return async_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx, func_args = split_ctx(args)
        forwarded = forward(ctx, func_args)
        _emit_sync(logger, _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx))
        started = time.perf_counter()
        try:
            guard(ctx, forwarded, kwargs)
            result = func(*forwarded, **kwargs)
        except Exception as exc:
            _emit_sync(logger, _error_event(tool_name, started, exc))
            raise
        _emit_sync(logger, _end_event(tool_name, started, result))
        return result

    _prepare_wrapper(sync_wrapper, func, prepend_ctx=prepend_ctx)
    return sync_wrapper


class ToolLoggerHub:
    """Fan-out de eventos de tools hacia uno o varios sinks.

    Es el punto de inyección entre engine y UI: ``sigil.py`` crea el hub, se lo
    pasa a ``ToolManager`` y a ``SigilApp``, y la app agrega su sink al montar.
    """

    def __init__(self, sinks: Sequence[ToolLogger] = ()) -> None:
        self._sinks: list[ToolLogger] = list(sinks)

    def add_sink(self, sink: ToolLogger) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: ToolLogger) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    def __call__(self, event: ToolCallEvent) -> Any:
        return self.emit(event)

    async def emit(self, event: ToolCallEvent) -> None:
        for sink in tuple(self._sinks):
            await _emit(sink, event)


class ToolRegistrar:
    """Registra tools en un agente aplicando el wrapper de logging."""

    def __init__(self, agent: Agent, logger: ToolLogger | None = None) -> None:
        self.agent = agent
        self.logger = logger

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorador equivalente a ``agent.tool_plain`` con logging.

        ``concurrency`` declara cuándo la llamada no puede convivir con las
        anteriores de la misma respuesta del modelo. Ver
        ``src.engine.tools.concurrency``.
        """
        return self._make(
            func, use_context=False, name=name, description=description, concurrency=concurrency, **tool_kwargs
        )

    def context_tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorador equivalente a ``agent.tool`` (recibe ``RunContext``)."""
        return self._make(
            func, use_context=True, name=name, description=description, concurrency=concurrency, **tool_kwargs
        )

    def register(
        self,
        func: Callable[..., Any],
        *,
        use_context: bool = False,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Callable[..., Any]:
        """Registra una función ya definida y devuelve el wrapper."""
        return self._make(
            func,
            use_context=use_context,
            name=name,
            description=description,
            concurrency=concurrency,
            **tool_kwargs,
        )

    def _make(
        self,
        func: Callable[..., Any] | None,
        *,
        use_context: bool,
        name: str | None,
        description: str | None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or target.__name__
            wrapped = wrap_tool(
                target,
                tool_name=tool_name,
                logger=self.logger,
                takes_ctx=use_context,
                concurrency=concurrency,
            )
            # Una tool con guarda necesita el contexto, así que se registra como
            # context tool aunque su función no lo pida.
            self._register(
                wrapped,
                use_context=use_context or concurrency is not None,
                name=name,
                description=description,
                **tool_kwargs,
            )
            return wrapped

        return decorator(func) if func is not None else decorator

    def _register(
        self,
        wrapped: Callable[..., Any],
        *,
        use_context: bool,
        name: str | None,
        description: str | None,
        **tool_kwargs: Any,
    ) -> None:
        options = {'name': name, 'description': description, **tool_kwargs}
        options = {key: value for key, value in options.items() if value is not None}
        register = self.agent.tool if use_context else self.agent.tool_plain
        if options:
            register(wrapped, **options)
        else:
            register(wrapped)


def register_tool(
    agent: Agent,
    func: Callable[..., Any] | None = None,
    *,
    logger: ToolLogger | None = None,
    use_context: bool = False,
    name: str | None = None,
    description: str | None = None,
    concurrency: ConcurrencyGuard | None = None,
    **tool_kwargs: Any,
) -> Any:
    """Atajo para registrar una tool suelta con logging."""
    registrar = ToolRegistrar(agent, logger)
    if func is None:

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            return registrar.register(
                target,
                use_context=use_context,
                name=name,
                description=description,
                concurrency=concurrency,
                **tool_kwargs,
            )

        return decorator

    return registrar.register(
        func,
        use_context=use_context,
        name=name,
        description=description,
        concurrency=concurrency,
        **tool_kwargs,
    )


__all__ = [
    'ConcurrencyContext',
    'ConcurrencyGuard',
    'ToolCallEvent',
    'ToolConflict',
    'ToolLogger',
    'ToolLoggerHub',
    'ToolRegistrar',
    'emit_tool_event',
    'preview',
    'register_tool',
    'wrap_tool',
]
