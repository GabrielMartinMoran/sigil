"""Tests for the model factories, the registry and the swapping router."""

from __future__ import annotations

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.wrapper import WrapperModel

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import CredentialError, ProviderError
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret
from src.engine.providers.factory.openai_builder import OpenAIKeyBuilder, OpenAIOAuthBuilder
from src.engine.providers.factory.opencode_builder import opencode_headers
from src.engine.providers.factory.openrouter_builder import OpenRouterBuilder
from src.engine.providers.factory.registry import BuilderRegistry, ModelRouter
from src.engine.providers.factory.unconfigured import NO_PROVIDER_MESSAGE, UnconfiguredModel

KEY = ApiKeySecret(key='sk-test')


def _account(kind: ProviderKind, variant=ProviderVariant.DEFAULT, auth=AuthMethod.API_KEY) -> ProviderAccount:
    return ProviderAccount.create(f'{kind}-{variant}', kind, variant, auth)


def _capability(name='model', dialect='chat-completions', **extra) -> ModelCapability:
    return ModelCapability(name=name, dialect=dialect, **extra)


# --- OpenRouter -------------------------------------------------------------


def test_openrouter_builder_produces_an_openrouter_model():
    account = _account(ProviderKind.OPENROUTER)
    model = OpenRouterBuilder().build(account, 'anthropic/x', _capability('anthropic/x'), KEY)
    assert isinstance(model, OpenRouterModel)
    assert model.model_name == 'anthropic/x'


def test_openrouter_builder_injects_the_window_so_the_status_bar_does_not_lie():
    account = _account(ProviderKind.OPENROUTER)
    capability = _capability('a/b', context_window=999_000, efforts=('low', 'high'))
    model = OpenRouterBuilder().build(account, 'a/b', capability, KEY)
    assert model.context_window == 999_000
    assert model.profile.get('supports_thinking') is True


def test_openrouter_builder_refuses_an_oauth_credential():
    oauth = OAuthSecret(access_token='a', refresh_token='r')
    try:
        OpenRouterBuilder().build(_account(ProviderKind.OPENROUTER), 'a/b', _capability(), oauth)
    except CredentialError as exc:
        assert 'API key' in str(exc)
    else:  # pragma: no cover
        raise AssertionError('expected CredentialError')


# --- OpenCode ---------------------------------------------------------------


def test_opencode_builder_uses_chat_completions_for_that_dialect():
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.ZEN)
    model = OpenCodeBuilder('sess-1').build(account, 'glm-5.3', _capability('glm-5.3'), KEY)
    assert isinstance(model, OpenAIChatModel)
    assert model.system == 'opencode'


def test_opencode_builder_uses_responses_when_the_catalogue_says_so():
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.ZEN)
    capability = _capability('gpt-5', dialect='responses', base_url='https://opencode.ai/inference/openai/v1')
    model = OpenCodeBuilder('sess-1').build(account, 'gpt-5', capability, KEY)
    assert isinstance(model, OpenAIResponsesModel)


def test_opencode_builder_uses_the_anthropic_sdk_for_the_messages_dialect():
    from pydantic_ai.models.anthropic import AnthropicModel

    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.ZEN)
    capability = _capability(
        'claude-sonnet-4-6', dialect='messages', base_url='https://opencode.ai/inference/anthropic/v1'
    )
    model = OpenCodeBuilder('sess-1').build(account, 'claude-sonnet-4-6', capability, KEY)
    assert isinstance(model, AnthropicModel)


def test_opencode_go_uses_its_own_base_url():
    from src.engine.providers.factory.opencode_builder import GO_BASE_URL, OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.GO)
    # A capability that claims another endpoint must not win over the Go route.
    capability = _capability('claude-haiku-4-5', base_url='https://opencode.ai/inference/anthropic/v1')
    model = OpenCodeBuilder('sess-1').build(account, 'claude-haiku-4-5', capability, KEY)
    assert str(model.provider.base_url).rstrip('/') == GO_BASE_URL.rstrip('/')


def test_opencode_zen_prefers_the_per_model_base_url():
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.ZEN)
    capability = _capability('m', base_url='https://example.test/v1')
    model = OpenCodeBuilder('sess-1').build(account, 'm', capability, KEY)
    assert 'example.test' in str(model.provider.base_url)


def test_opencode_go_sends_the_session_header_the_endpoint_requires():
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.GO)
    model = OpenCodeBuilder('conversation-42').build(account, 'glm-5.3', _capability('glm-5.3'), KEY)
    headers = model.provider.client.default_headers
    assert headers['x-opencode-session'] == 'conversation-42'
    assert headers['User-Agent'].startswith('sigil/')


def test_opencode_headers_helper_is_the_single_source_of_the_session_header():
    assert opencode_headers('abc') == {'User-Agent': 'sigil/0.1.0', 'x-opencode-session': 'abc'}


def test_opencode_profile_declares_the_reasoning_field():
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    account = _account(ProviderKind.OPENCODE, ProviderVariant.GO)
    capability = _capability('m', context_window=200_000, efforts=('low', 'high'))
    model = OpenCodeBuilder('s').build(account, 'm', capability, KEY)
    assert model.context_window == 200_000
    assert model.profile.get('openai_chat_thinking_field') == 'reasoning_content'


# --- OpenAI -----------------------------------------------------------------


def test_openai_key_builder_produces_a_responses_model():
    account = _account(ProviderKind.OPENAI)
    model = OpenAIKeyBuilder().build(account, 'gpt-5', _capability('gpt-5'), KEY)
    assert isinstance(model, OpenAIResponsesModel)
    assert model.system == 'openai'


def test_openai_key_builder_refuses_an_oauth_credential():
    oauth = OAuthSecret(access_token='a', refresh_token='r')
    try:
        OpenAIKeyBuilder().build(_account(ProviderKind.OPENAI), 'gpt-5', _capability(), oauth)
    except CredentialError:
        pass
    else:  # pragma: no cover
        raise AssertionError('expected CredentialError')


def test_openai_oauth_builder_uses_the_injected_provider():
    seen: list[tuple] = []

    def factory(credential, account):
        from pydantic_ai.providers.openai import OpenAIProvider

        seen.append((credential, account.alias))
        return OpenAIProvider(api_key='from-oauth')

    oauth = OAuthSecret(access_token='a', refresh_token='r', client_id='c')
    model = OpenAIOAuthBuilder(factory).build(
        _account(ProviderKind.OPENAI, auth=AuthMethod.OAUTH), 'gpt-5', _capability(), oauth
    )

    assert isinstance(model, OpenAIResponsesModel)
    assert seen and seen[0][1] == 'openai-default'


def test_chatgpt_plan_sends_current_effort_without_configuration_updates():
    import asyncio

    from pydantic_ai.messages import ModelRequest, ModelResponse, ModelSettingsDeltaPart, TextPart, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.providers.openai import OpenAIProvider

    account = _account(ProviderKind.OPENAI, auth=AuthMethod.OAUTH)
    capability = _capability('gpt-6.1-sol', efforts=('low', 'high'))
    model = OpenAIOAuthBuilder(lambda credential, account: OpenAIProvider(api_key='test')).build(
        account, 'gpt-6.1-sol', capability, OAuthSecret(access_token='a', refresh_token='r')
    )
    messages = [
        ModelRequest(
            parts=[ModelSettingsDeltaPart(settings={'thinking': 'low'}, top_level=True), UserPromptPart('go')]
        ),
        ModelResponse(parts=[TextPart('answer')], model_name='gpt-6.1-terra'),
        ModelRequest(parts=[ModelSettingsDeltaPart(settings={'thinking': 'high'}), UserPromptPart('continue')]),
    ]
    request = asyncio.run(
        model._build_responses_request_params(messages, {}, ModelRequestParameters(thinking='high'), model.profile)
    )

    assert all(item.get('type') != 'configuration_update' for item in request.input)
    assert request.reasoning['effort'] == 'high'
    assert isinstance(messages[-1].parts[0], ModelSettingsDeltaPart)

    key_model = OpenAIKeyBuilder().build(_account(ProviderKind.OPENAI), 'gpt-6.1-sol', capability, KEY)
    key_request = asyncio.run(
        key_model._build_responses_request_params(
            messages, {}, ModelRequestParameters(thinking='high'), key_model.profile
        )
    )
    assert any(item.get('type') == 'configuration_update' for item in key_request.input)
    assert key_request.reasoning['effort'] == 'low'


# --- registry ---------------------------------------------------------------


def _registry() -> BuilderRegistry:
    from src.engine.providers.factory.opencode_builder import OpenCodeBuilder

    return BuilderRegistry(
        (
            OpenRouterBuilder(),
            OpenCodeBuilder('s'),
            OpenAIKeyBuilder(),
            OpenAIOAuthBuilder(lambda credential, account: None),
        )
    )


def test_registry_picks_by_kind_and_credential():
    registry = _registry()
    assert isinstance(registry.for_account(_account(ProviderKind.OPENROUTER), KEY), OpenRouterBuilder)
    assert isinstance(
        registry.for_account(
            _account(ProviderKind.OPENAI, auth=AuthMethod.OAUTH), OAuthSecret(access_token='a', refresh_token='r')
        ),
        OpenAIOAuthBuilder,
    )
    assert isinstance(registry.for_account(_account(ProviderKind.OPENAI), KEY), OpenAIKeyBuilder)


def test_registry_reports_an_unserviceable_pair():
    registry = BuilderRegistry((OpenRouterBuilder(),))
    try:
        registry.for_account(_account(ProviderKind.OPENAI), KEY)
    except ProviderError as exc:
        assert 'OpenAI' in str(exc)
    else:  # pragma: no cover
        raise AssertionError('expected ProviderError')


# --- router -----------------------------------------------------------------


def test_router_is_a_wrapper_that_swaps_and_delegates():
    first = TestModel(custom_output_text='one')
    second = TestModel(custom_output_text='two')
    router = ModelRouter(first)

    assert isinstance(router, WrapperModel)
    assert router.model_name == first.model_name
    router.use(second)
    assert router.current is second
    assert router.model_name == second.model_name


def test_router_exposes_the_context_window_of_the_wrapped_model():
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider('x', base_url='https://x/v1', api_key='k')
    model = OpenAIChatModel('m', provider=provider)
    router = ModelRouter(UnconfiguredModel())
    assert router.context_window is None
    router.use(model)
    assert router.context_window == model.context_window


def test_unconfigured_model_refuses_a_request_with_an_actionable_message():
    import asyncio

    from pydantic_ai import Agent

    from src.engine.providers.factory.unconfigured import NoProviderError

    async def inner():
        agent = Agent(UnconfiguredModel(), instructions='x')
        try:
            await agent.run('hi')
        except NoProviderError as exc:
            assert str(exc) == NO_PROVIDER_MESSAGE
        else:  # pragma: no cover
            raise AssertionError('expected NoProviderError')

    asyncio.run(inner())


def test_openrouter_claude_enables_cache_without_changing_other_models():
    account = _account(ProviderKind.OPENROUTER)
    claude = OpenRouterBuilder().build(account, 'anthropic/claude-sonnet-4.6', _capability(), KEY)
    other = OpenRouterBuilder().build(account, 'openai/gpt-5.1', _capability(), KEY)
    assert claude.settings is not None
    assert claude.settings['openrouter_cache_instructions'] is True
    assert claude.settings['openrouter_cache_tool_definitions'] is True
    assert claude.settings['openrouter_cache_messages'] is True
    assert not other.settings


def test_claude_openrouter_serializes_cache_breakpoints_and_keeps_tool_order(monkeypatch):
    """Exercise the real adapter without sending credentials or traffic."""
    import asyncio
    import json

    import httpx2
    from pydantic_ai import Agent
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                'id': 'test',
                'provider': 'test',
                'model': 'anthropic/claude-sonnet-4.6',
                'object': 'chat.completion',
                'created': 1,
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}],
                'usage': {'prompt_tokens': 1200, 'completion_tokens': 1, 'total_tokens': 1201},
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
    monkeypatch.setattr(
        'pydantic_ai.providers.openrouter.OpenRouterProvider',
        lambda **kwargs: OpenRouterProvider(api_key='test', http_client=client),
    )
    model = OpenRouterBuilder().build(
        _account(ProviderKind.OPENROUTER), 'anthropic/claude-sonnet-4.6', _capability(), KEY
    )
    agent = Agent(model, instructions='stable instructions')

    @agent.tool_plain
    def alpha() -> str:
        return 'a'

    @agent.tool_plain
    def beta() -> str:
        return 'b'

    async def scenario():
        first = await agent.run('first')
        await agent.run('second', message_history=first.all_messages())
        await client.aclose()

    asyncio.run(scenario())
    assert len(requests) == 2
    for request in requests:
        assert [tool['function']['name'] for tool in request['tools']] == ['alpha', 'beta']
        assert request['tools'][-1]['cache_control']['type'] == 'ephemeral'
        assert any(
            'cache_control' in part
            for message in request['messages']
            for part in message.get('content', [])
            if isinstance(part, dict)
        )
