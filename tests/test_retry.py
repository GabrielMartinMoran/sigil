"""Reintentos ante fallos transitorios del modelo."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAuthenticationError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelServerError,
    RetryModelRequest,
)
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.engine.agents.retry import (
    RetryTransientModelErrors,
    describe_error,
    is_transient,
)

_BODY = 'Upstream error from Morph: regional pod proxy response stream failed: error decoding response body'

# Así es como los adaptadores reales levantan un 502: un error que es a la vez
# ``ModelHTTPError`` y la categoría. Un ``ModelHTTPError`` pelado no satisface
# ``except ModelServerError``, así que construir el fixture con ``for_category``
# es lo que hace que los tests midan el caso real.
_SERVER_ERROR = ModelHTTPError.for_category(ModelServerError, status_code=502, model_name='test/model', body=_BODY)

# Error sin categoría: sólo adaptadores o modelos propios que no clasifican.
_UNCATEGORIZED_502 = ModelHTTPError(502, 'test/model', body=_BODY)
_UNCATEGORIZED_404 = ModelHTTPError(404, 'test/model', body='not found')


def _capability(**kwargs: object) -> RetryTransientModelErrors:
    # Sin espera real entre intentos: los tests no deben dormir.
    return RetryTransientModelErrors(base_delay=0.0, **kwargs)  # type: ignore[arg-type]


def _hook(capability: RetryTransientModelErrors, attempt: int, error: Exception) -> None:
    """Corre el hook como lo hace el framework, con el intento que falló."""

    async def call() -> None:
        await capability.on_model_request_error(
            SimpleNamespace(),  # type: ignore[arg-type]
            request_context=SimpleNamespace(attempt=attempt),  # type: ignore[arg-type]
            error=error,
        )

    asyncio.run(call())


def test_transient_errors_trigger_another_attempt() -> None:
    capability = _capability(max_attempts=5)

    for attempt in range(1, 5):
        with pytest.raises(RetryModelRequest):
            _hook(capability, attempt, _SERVER_ERROR)


def test_budget_exhausted_propagates_original_error() -> None:
    capability = _capability(max_attempts=5)

    with pytest.raises(ModelServerError):
        _hook(capability, 5, _SERVER_ERROR)


@pytest.mark.parametrize(
    'error',
    [ModelAuthenticationError('test/model', 'invalid key'), ModelQuotaExceededError('test/model', 'no credits')],
)
def test_permanent_errors_are_not_retried(error: Exception) -> None:
    assert is_transient(error) is False
    with pytest.raises(type(error)):
        _hook(_capability(max_attempts=5), 1, error)


def test_rate_limit_and_server_errors_are_transient() -> None:
    assert is_transient(ModelRateLimitError('test/model', 'too many requests')) is True
    assert is_transient(_SERVER_ERROR) is True


def test_uncategorized_http_error_falls_back_to_status_code() -> None:
    # Sin categoría: el status decide. 502 vale otro intento, 404 no.
    assert is_transient(_UNCATEGORIZED_502) is True
    assert is_transient(_UNCATEGORIZED_404) is False


def test_quota_error_with_429_status_is_not_retried() -> None:
    # Regresión: OpenRouter manda la cuota agotada con 429. Comparte status con
    # el rate limit, pero es permanente: no debe consumir los cinco intentos.
    quota = ModelHTTPError.for_category(ModelQuotaExceededError, status_code=429, model_name='test/model')

    assert is_transient(quota) is False


def test_retry_notice_reports_attempt_and_reason() -> None:
    notices = []
    capability = _capability(max_attempts=4)
    capability.on_retry = notices.append

    with pytest.raises(RetryModelRequest):
        _hook(capability, 2, _SERVER_ERROR)

    assert len(notices) == 1
    notice = notices[0]
    assert (notice.attempt, notice.next_attempt, notice.max_attempts) == (2, 3, 4)
    assert notice.delay == 0.0
    assert 'HTTP 502' in notice.reason
    assert 'error decoding response body' in notice.reason


def test_notifier_failure_does_not_break_the_retry() -> None:
    def boom(_notice: object) -> None:
        raise RuntimeError('la UI explotó')

    capability = _capability(max_attempts=5)
    capability.on_retry = boom

    with pytest.raises(RetryModelRequest):
        _hook(capability, 1, _SERVER_ERROR)


def test_delay_honours_retry_after_and_cap() -> None:
    capability = RetryTransientModelErrors(base_delay=1.0, max_delay=8.0)

    assert capability.delay_for(1, retry_after=3.0) == 3.0
    assert capability.delay_for(1, retry_after=600.0) == 8.0
    assert capability.delay_for(1, retry_after=-5.0) == 0.0
    assert 0.0 <= capability.delay_for(10) <= 8.0


def test_describe_error_summarizes_status_and_body() -> None:
    described = describe_error(_SERVER_ERROR)

    assert described.startswith('HTTP 502 · Upstream error from Morph')
    assert '\n' not in described


def test_agent_retries_transient_errors_until_success() -> None:
    calls = {'count': 0}

    def model_function(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls['count'] += 1
        if calls['count'] <= 2:
            raise _SERVER_ERROR
        return ModelResponse(parts=[TextPart('respuesta final')])

    async def inner() -> None:
        capability = _capability(max_attempts=5)
        notices = []
        capability.on_retry = notices.append
        agent = Agent(FunctionModel(model_function), instructions='x', capabilities=[capability])

        result = await agent.run('hola')

        assert result.output == 'respuesta final'
        assert calls['count'] == 3
        assert [notice.attempt for notice in notices] == [1, 2]

    asyncio.run(inner())


def test_agent_fails_after_exhausting_attempts() -> None:
    calls = {'count': 0}

    def model_function(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        calls['count'] += 1
        raise _SERVER_ERROR

    async def inner() -> None:
        capability = _capability(max_attempts=3)
        agent = Agent(FunctionModel(model_function), instructions='x', capabilities=[capability])

        with pytest.raises(ModelServerError):
            await agent.run('hola')

        assert calls['count'] == 3

    asyncio.run(inner())
