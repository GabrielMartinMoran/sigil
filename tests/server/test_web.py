"""Tests de las tools de web (sin red)."""

import asyncio

from src.engine.web.tools import register_web_tools


class DummyAgent:
    def __init__(self) -> None:
        self.tools = {}

    def _register(self, func, kwargs):
        name = kwargs.get('name') or func.__name__
        self.tools[name] = func
        return func

    def tool_plain(self, func, **kwargs):
        return self._register(func, kwargs)

    def tool(self, func, **kwargs):
        return self._register(func, kwargs)


class FakeDDGS:
    def text(self, query, **kwargs):
        return [
            {'title': 'Result 1', 'href': 'https://example.com/1', 'body': 'Snippet 1'},
            {'title': 'Result 2', 'url': 'https://example.com/2', 'snippet': 'Snippet 2'},
        ]

    def extract(self, url, fmt='text_markdown'):
        return {'url': url, 'content': '# Title\n\nBody'}


def _tools(monkeypatch):
    monkeypatch.setattr('src.engine.web.tools.DDGS', FakeDDGS)
    agent = DummyAgent()
    register_web_tools(agent)
    return agent.tools


def test_web_search_maps_results(monkeypatch):
    tools = _tools(monkeypatch)
    result = asyncio.run(tools['web_search'](query='python', max_results=2))
    assert result.query == 'python'
    assert result.count == 2
    assert result.results[0].title == 'Result 1'
    assert result.results[0].url == 'https://example.com/1'
    assert result.results[1].url == 'https://example.com/2'


def test_read_url_returns_markdown(monkeypatch):
    tools = _tools(monkeypatch)
    result = asyncio.run(tools['read_url'](url='https://example.com'))
    assert result.url == 'https://example.com'
    assert result.format == 'text_markdown'
    assert result.content.startswith('# Title')
