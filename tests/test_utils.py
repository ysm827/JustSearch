"""
Basic tests for JustSearch utility functions.
Run with: python -m pytest tests/ -v
"""

import pytest
from types import SimpleNamespace

from backend.app.llm_client import (
    LLMClient,
    LLMProviderConfigurationError,
    ensure_live_artifact_answer,
    _provider_error_message,
)
from backend.app.openai_client import LOCAL_PROVIDER_API_KEY, create_openai_client


def test_openai_client_uses_placeholder_for_empty_local_api_key():
    client = create_openai_client(
        api_key="",
        base_url="http://host.docker.internal:11434/v1",
    )

    assert client.api_key == LOCAL_PROVIDER_API_KEY


class TestLLMContextMessages:
    def test_full_history_and_assistant_content_are_preserved(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        long_answer = "这是一段很长的 assistant 内容。" * 300
        history = [
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": long_answer},
            {"role": "user", "content": "第二轮"},
            {"role": "assistant", "content": "短回复"},
            {"role": "user", "content": "第三轮"},
        ]

        context = client._build_context_messages(history)

        assert context == history
        assert context[1]["content"] == long_answer
        assert "答案已截断" not in context[1]["content"]


class TestRateLimiter:
    def _make_limiter(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from backend.app.rate_limiter import RateLimiter
        return RateLimiter(max_requests=3, window_seconds=60)

    def test_basic_rate_limiting(self):
        limiter = self._make_limiter()
        for _ in range(3):
            allowed, retry = limiter.check("test_key")
            assert allowed is True

        allowed, retry = limiter.check("test_key")
        assert allowed is False
        assert retry > 0

    def test_different_keys_independent(self):
        limiter = self._make_limiter()
        limiter.check("key_a")
        limiter.check("key_a")
        limiter.check("key_a")

        allowed_a, _ = limiter.check("key_a")
        assert allowed_a is False

        allowed_b, _ = limiter.check("key_b")
        assert allowed_b is True

    def test_cleanup(self):
        import time
        from backend.app.rate_limiter import RateLimiter
        limiter = RateLimiter(max_requests=5, window_seconds=1)

        limiter.check("test_key")
        time.sleep(1.1)
        limiter.cleanup()

        allowed, _ = limiter.check("test_key")
        assert allowed is True


class TestLLMResponseParsing:
    def test_provider_error_message_maps_subscription_failure(self):
        error = Exception(
            "Error code: 403 - {'code': 'SUBSCRIPTION_NOT_FOUND', "
            "'message': 'No active subscription found for this group'}"
        )

        assert "没有可用订阅" in _provider_error_message(error)

    def test_extract_response_content_accepts_sse_text_response(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        response = (
            'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{"content":"{\\"type\\":\\"search\\","},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{"content":"\\"queries\\":[\\"MDN delete\\"]}"},"finish_reason":null}]}\n\n'
            "data: [DONE]\n\n"
        )

        assert client._extract_response_content(response) == (
            '{"type":"search","queries":["MDN delete"]}'
        )

    def test_analyze_task_accepts_plain_string_response(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        calls = []

        async def fake_call(*args, **kwargs):
            calls.append(kwargs)
            return '{"type": "search", "queries": ["URLSearchParams delete MDN"]}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(client.analyze_task("what does URLSearchParams.delete do"))

        assert result == {
            "type": "search",
            "queries": ["URLSearchParams delete MDN"],
        }
        assert calls[0]["retries"] == 0

    def test_assess_relevance_accepts_plain_string_response(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        calls = []

        async def fake_call(*args, **kwargs):
            calls.append(kwargs)
            return '{"relevant_ids": [2, 4]}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(
            client.assess_relevance(
                "FastAPI CORS",
                [
                    {"id": 1, "title": "Generic FastAPI", "snippet": "..."},
                    {"id": 2, "title": "FastAPI CORS", "snippet": "..."},
                    {"id": 4, "title": "CORSMiddleware", "snippet": "..."},
                ],
            )
        )

        assert result == [2, 4]
        assert calls[0]["retries"] == 0

    def test_analyze_task_propagates_provider_configuration_errors(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
            raise LLMProviderConfigurationError("模型服务返回 403：当前 API Key 所属账户没有可用订阅。")

        client._call_with_retry = fake_call

        import asyncio
        with pytest.raises(LLMProviderConfigurationError, match="没有可用订阅"):
            asyncio.run(client.analyze_task("subscription check"))

    def test_assess_relevance_propagates_provider_configuration_errors(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
            raise LLMProviderConfigurationError("模型服务返回 403：当前 API Key 所属账户没有可用订阅。")

        client._call_with_retry = fake_call

        import asyncio
        with pytest.raises(LLMProviderConfigurationError, match="没有可用订阅"):
            asyncio.run(
                client.assess_relevance(
                    "subscription check",
                    [{"id": 1, "title": "A", "snippet": "..."}],
                )
            )

    def test_analyze_task_treats_string_queries_as_one_query(self):
        from backend.app import llm_client as llm_module

        llm_module._ANALYSIS_CACHE.clear()
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
            return '{"queries": "URLSearchParams delete MDN"}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(client.analyze_task("format drift query"))

        assert result == {
            "type": "search",
            "queries": ["URLSearchParams delete MDN"],
        }
        llm_module._ANALYSIS_CACHE.clear()

    def test_assess_relevance_parses_multi_digit_ids_from_string(self):
        from backend.app import llm_client as llm_module

        llm_module._ANALYSIS_CACHE.clear()
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
            return '{"relevant_ids": "10, 12"}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(
            client.assess_relevance(
                "multi digit ids",
                [
                    {"id": 10, "title": "A", "snippet": "..."},
                    {"id": 12, "title": "B", "snippet": "..."},
                ],
            )
        )

        assert result == [10, 12]
        llm_module._ANALYSIS_CACHE.clear()

    def test_decide_click_elements_keeps_string_id_intact(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
            return '{"clicked_ids": "js-interact-10"}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(
            client.decide_click_elements(
                "open details",
                [
                    {"id": "js-interact-10", "tag": "button", "text": "Read more"},
                    {"id": "js-interact-11", "tag": "button", "text": "Share"},
                ],
            )
        )

        assert result == ["js-interact-10"]

    def test_extract_json_ignores_braces_inside_strings(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        result = client._extract_json(
            'Sure: {"type": "search", "queries": ["literal {brace} query"]} done'
        )

        assert result == {"type": "search", "queries": ["literal {brace} query"]}

    def test_analyze_task_cache_is_scoped_by_history(self):
        import asyncio
        from backend.app import llm_client as llm_module

        llm_module._ANALYSIS_CACHE.clear()
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        calls = []

        async def fake_call(messages, **kwargs):
            calls.append(messages)
            history_text = "\n".join(msg["content"] for msg in messages)
            if "Alice" in history_text:
                return '{"type": "search", "queries": ["Alice context"]}'
            return '{"type": "search", "queries": ["Bob context"]}'

        client._call_with_retry = fake_call

        first = asyncio.run(
            client.analyze_task(
                "where is it?",
                [{"role": "user", "content": "Alice"}],
            )
        )
        second = asyncio.run(
            client.analyze_task(
                "where is it?",
                [{"role": "user", "content": "Bob"}],
            )
        )

        assert first["queries"] == ["Alice context"]
        assert second["queries"] == ["Bob context"]
        assert len(calls) == 2
        llm_module._ANALYSIS_CACHE.clear()

    def test_relevance_cache_is_scoped_by_snippet_content(self):
        import asyncio
        from backend.app import llm_client as llm_module

        llm_module._ANALYSIS_CACHE.clear()
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        calls = []

        async def fake_call(*args, **kwargs):
            calls.append(args)
            return '{"relevant_ids": [%d]}' % (2 if len(calls) == 1 else 4)

        client._call_with_retry = fake_call

        first = asyncio.run(
            client.assess_relevance(
                "same query",
                [
                    {"id": 1, "title": "A", "url": "https://a.example", "snippet": "alpha"},
                    {"id": 2, "title": "B", "url": "https://b.example", "snippet": "beta"},
                ],
            )
        )
        second = asyncio.run(
            client.assess_relevance(
                "same query",
                [
                    {"id": 3, "title": "C", "url": "https://c.example", "snippet": "gamma"},
                    {"id": 4, "title": "D", "url": "https://d.example", "snippet": "delta"},
                ],
            )
        )

        assert first == [2]
        assert second == [4]
        assert len(calls) == 2
        llm_module._ANALYSIS_CACHE.clear()


class TestLiveArtifactsAnswerFormatting:
    def test_markdown_fallback_is_wrapped_as_inline_live_artifact(self):
        answer = "## 核心结论\n- **重点**：已开启 Live Artifacts [1]\n- 保留引用 [2]"

        artifact = ensure_live_artifact_answer(answer)

        assert artifact.startswith('<section style="display:block;width:100%;')
        assert "<h2>核心结论</h2>" in artifact
        assert "<li><strong>重点</strong>：已开启 Live Artifacts [1]</li>" in artifact
        assert "##" not in artifact

    def test_fenced_html_is_unwrapped(self):
        artifact = ensure_live_artifact_answer(
            "```html\n<section style=\"display:block;width:100%;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere;\"><h2>Ready</h2></section>\n```"
        )

        assert artifact.startswith("<section")
        assert "```" not in artifact

    def test_fenced_full_html_document_is_preserved(self):
        artifact = ensure_live_artifact_answer(
            "```html\n<!doctype html><html><head><title>Demo</title></head><body><main>Ready</main></body></html>\n```"
        )

        assert artifact.startswith("<!doctype html>")
        assert "<main>Ready</main>" in artifact
        assert "&lt;html" not in artifact

    def test_generate_answer_uses_live_artifacts_prompt_and_fallback(self):
        import asyncio

        captured = {}

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                self._iter = iter(self._chunks)
                return self

            async def __anext__(self):
                try:
                    content = next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=content),
                            finish_reason=None,
                        )
                    ]
                )

        class FakeCompletions:
            async def create(self, model, messages, stream):
                captured["messages"] = messages
                return FakeStream(
                    [
                        "Status: sufficient\nMissing_Info: \nAnswer:\n",
                        "## 结论\n- 普通 Markdown 会被兜底转换 [1]",
                    ]
                )

        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        result = asyncio.run(
            client.generate_answer(
                "测试 Live Artifacts",
                [{"id": 1, "title": "fixture", "content": "source", "url": "https://example.test"}],
                live_artifacts_mode=True,
            )
        )

        system_prompt = captured["messages"][0]["content"]
        assert "[Live Artifacts Inline Protocol - zh]" in system_prompt
        assert "The actual answer content in Markdown" not in system_prompt
        assert result["answer"].startswith('<section style="display:block;width:100%;')
        assert "<h2>结论</h2>" in result["answer"]

    def test_live_artifacts_markdown_fallback_is_not_streamed_as_markdown(self):
        import asyncio

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                self._iter = iter(self._chunks)
                return self

            async def __anext__(self):
                try:
                    content = next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=content),
                            finish_reason=None,
                        )
                    ]
                )

        class FakeCompletions:
            async def create(self, model, messages, stream):
                return FakeStream(
                    [
                        "Status: sufficient\nMissing_Info: \nAnswer:\n",
                        "## 结论\n- Markdown 兜底最终会转为 artifact [1]",
                    ]
                )

        chunks = []
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        result = asyncio.run(
            client.generate_answer(
                "测试 Live Artifacts",
                [{"id": 1, "title": "fixture", "content": "source", "url": "https://example.test"}],
                stream_callback=chunks.append,
                live_artifacts_mode=True,
            )
        )

        assert chunks == []
        assert result["answer"].startswith('<section style="display:block;width:100%;')

    def test_live_artifacts_fenced_html_streams_for_preview(self):
        import asyncio

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                self._iter = iter(self._chunks)
                return self

            async def __anext__(self):
                try:
                    content = next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=content),
                            finish_reason=None,
                        )
                    ]
                )

        class FakeCompletions:
            async def create(self, model, messages, stream):
                return FakeStream(
                    [
                        "Status: sufficient\nMissing_Info: \nAnswer:\n",
                        "```html\n",
                        "<section style=\"display:block;width:100%\"><h2>Live</h2></section>\n```",
                    ]
                )

        chunks = []
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        result = asyncio.run(
            client.generate_answer(
                "测试 Live Artifacts",
                [{"id": 1, "title": "fixture", "content": "source", "url": "https://example.test"}],
                stream_callback=chunks.append,
                live_artifacts_mode=True,
            )
        )

        streamed = "".join(chunks)
        assert "```html" in streamed
        assert "<section" in streamed
        assert result["answer"].startswith("<section")
