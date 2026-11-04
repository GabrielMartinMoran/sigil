"""Wrapper que loguea y aplica guardas de concurrencia a una tool.

Envolver la función —en vez de pedirle a cada tool que loguee a mano— mantiene
las tools enfocadas en su dominio. El wrapper conserva la naturaleza sync/async
de la función original para no cambiar cómo la ejecuta pydantic-ai ni romper
llamadas directas desde otros módulos.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic_ai import RunContext

from src.engine.tooling.concurrency import (
    ConcurrencyContext,
    ConcurrencyGuard,
    ToolConflict,
    earlier_siblings,
)
from src.engine.tooling.events import ToolCallEvent, preview
from src.engine.tooling.logging import ToolLogger, emit_tool_event, emit_tool_event_sync


def _bind_args(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    drop_first: bool,
) -> dict[str, Any]:
    """Argumentos efectivos de la llamada, con los defaults aplicados."""
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

    ``functools.wraps`` copia ``__annotations__``, pero si el módulo original usa
    ``from __future__ import annotations`` quedan como strings y pydantic-ai las
    resolvería contra los globals del wrapper. Las evaluamos acá y dejamos tipos
    reales en ``__annotations__`` y ``__signature__``.

    Con ``prepend_ctx`` la firma expuesta arranca con un ``RunContext``: es lo que
    necesita una tool con guarda de concurrencia, que recibe el contexto aunque
    su función no lo pida. pydantic-ai lo saca del schema porque la tool se
    registra como context tool.
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


def _start_event(
    tool_name: str,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    drop_first: bool,
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

    ``takes_ctx`` indica que ``func`` recibe un ``RunContext`` como primer
    argumento. Con ``concurrency`` el wrapper recibe el contexto aunque ``func``
    no lo pida, para poder evaluar la guarda antes de ejecutar.
    """
    wrapper_takes_ctx = takes_ctx or concurrency is not None
    prepend_ctx = concurrency is not None and not takes_ctx

    def split_ctx(args: tuple[Any, ...]) -> tuple[Any, tuple[Any, ...]]:
        """Separa el ``RunContext`` del wrapper de los argumentos de ``func``.

        Una llamada directa desde código —tests, otros módulos— no trae contexto.
        En ese caso se ejecuta igual, sin guarda: la guarda sólo tiene sentido
        dentro de un run, donde hay hermanos con los que comparar.
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
            await emit_tool_event(logger, _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx))
            started = time.perf_counter()
            try:
                guard(ctx, forwarded, kwargs)
                result = await func(*forwarded, **kwargs)
            except Exception as exc:
                await emit_tool_event(logger, _error_event(tool_name, started, exc))
                raise
            await emit_tool_event(logger, _end_event(tool_name, started, result))
            return result

        _prepare_wrapper(async_wrapper, func, prepend_ctx=prepend_ctx)
        return async_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx, func_args = split_ctx(args)
        forwarded = forward(ctx, func_args)
        emit_tool_event_sync(logger, _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx))
        started = time.perf_counter()
        try:
            guard(ctx, forwarded, kwargs)
            result = func(*forwarded, **kwargs)
        except Exception as exc:
            emit_tool_event_sync(logger, _error_event(tool_name, started, exc))
            raise
        emit_tool_event_sync(logger, _end_event(tool_name, started, result))
        return result

    _prepare_wrapper(sync_wrapper, func, prepend_ctx=prepend_ctx)
    return sync_wrapper
