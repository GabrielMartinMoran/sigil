"""Wrapper that logs and applies concurrency guards to a tool.

Wrapping the function (instead of asking every tool to log by hand) keeps
the tools focused on their domain. The wrapper preserves the sync/async nature
of the original function so as not to change how pydantic-ai runs it nor break
direct calls from other modules.
"""

from __future__ import annotations

import functools
import inspect
import time
import uuid
from collections.abc import Callable, Mapping
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
from src.engine.tooling.outputs import present_output
from src.engine.tooling.presentation import Describe, summary_of


def _bind_args(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    drop_first: bool,
) -> dict[str, Any]:
    """Effective args of the call, with the defaults applied."""
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
    """Preserves name/docstring/signature and resolves string annotations.

    ``functools.wraps`` copies ``__annotations__``, but if the original module uses
    ``from __future__ import annotations`` they stay as strings and pydantic-ai would
    resolve them against the wrapper's globals. We evaluate them here and leave real
    types in ``__annotations__`` and ``__signature__``.

    With ``prepend_ctx`` the exposed signature starts with a ``RunContext``: it is what
    a tool with a concurrency guard needs, since it receives the context even though
    its function does not ask for it. pydantic-ai strips it from the schema because the tool
    is registered as a context tool.
    """
    functools.update_wrapper(wrapper, func)
    try:
        annotations = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 - fallback to the original annotations
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


def _summary_args(
    func: Callable[..., Any],
    forwarded: tuple[Any, ...],
    kwargs: dict[str, Any],
    drop_first: bool,
) -> dict[str, Any]:
    """The args a presenter sees: the effective ones, defaults included.

    Bound at the end of the call and not at the start, because a tool without a
    presenter must not pay for a signature bind it will never use.
    """
    return _bind_args(func, forwarded, kwargs, drop_first=drop_first)


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
        invocation_id=uuid.uuid4().hex,
        args=_bind_args(func, args, kwargs, drop_first=drop_first),
    )


def _end_event(
    tool_name: str,
    started: float,
    result: Any,
    *,
    describe: Describe | None = None,
    args: Mapping[str, Any] | None = None,
    invocation_id: str = '',
) -> ToolCallEvent:
    shown = present_output(tool_name, result)
    return ToolCallEvent(
        tool_name=tool_name,
        phase='success',
        invocation_id=invocation_id,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        result_preview=preview(result),
        summary=summary_of(describe, args or {}, result) or shown.summary,
        output=shown.text,
    )


def _error_event(tool_name: str, started: float, exc: BaseException, invocation_id: str = '') -> ToolCallEvent:
    return ToolCallEvent(
        tool_name=tool_name,
        phase='error',
        invocation_id=invocation_id,
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
    """Evaluates the guard and raises ``ToolConflict`` if the call cannot run."""
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
    describe: Describe | None = None,
) -> Callable[..., Any]:
    """Wraps ``func`` to log start, success and error.

    ``takes_ctx`` indicates that ``func`` receives a ``RunContext`` as its first
    argument. With ``concurrency`` the wrapper receives the context even if ``func``
    does not ask for it, so the guard can be evaluated before running.

    ``describe`` is the tool's own one-line summary of its result, declared next to
    the tool like its ``permission``. It is resolved only when the call succeeds, and
    its args are bound only when a presenter exists: a tool without one pays nothing.
    """
    wrapper_takes_ctx = takes_ctx or concurrency is not None
    prepend_ctx = concurrency is not None and not takes_ctx

    def split_ctx(args: tuple[Any, ...]) -> tuple[Any, tuple[Any, ...]]:
        """Splits the wrapper's ``RunContext`` from the args of ``func``.

        A direct call from code (tests, other modules) brings no context.
        In that case it runs anyway, without a guard: the guard only makes sense
        inside a run, where there are siblings to compare against.
        """
        if wrapper_takes_ctx and args and isinstance(args[0], RunContext):
            return args[0], args[1:]
        return None, args

    def forward(ctx: Any, func_args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Args with which ``func`` is invoked."""
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
            announcement = _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx)
            await emit_tool_event(logger, announcement)
            started = time.perf_counter()
            try:
                guard(ctx, forwarded, kwargs)
                result = await func(*forwarded, **kwargs)
            except Exception as exc:
                await emit_tool_event(logger, _error_event(tool_name, started, exc, announcement.invocation_id))
                raise
            await emit_tool_event(
                logger,
                _end_event(
                    tool_name,
                    started,
                    result,
                    describe=describe,
                    args=_summary_args(func, forwarded, kwargs, takes_ctx),
                    invocation_id=announcement.invocation_id,
                ),
            )
            return result

        _prepare_wrapper(async_wrapper, func, prepend_ctx=prepend_ctx)
        return async_wrapper

    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx, func_args = split_ctx(args)
        forwarded = forward(ctx, func_args)
        announcement = _start_event(tool_name, func, forwarded, kwargs, drop_first=takes_ctx)
        emit_tool_event_sync(logger, announcement)
        started = time.perf_counter()
        try:
            guard(ctx, forwarded, kwargs)
            result = func(*forwarded, **kwargs)
        except Exception as exc:
            emit_tool_event_sync(logger, _error_event(tool_name, started, exc, announcement.invocation_id))
            raise
        emit_tool_event_sync(
            logger,
            _end_event(
                tool_name,
                started,
                result,
                describe=describe,
                args=_summary_args(func, forwarded, kwargs, takes_ctx),
                invocation_id=announcement.invocation_id,
            ),
        )
        return result

    _prepare_wrapper(sync_wrapper, func, prepend_ctx=prepend_ctx)
    return sync_wrapper
