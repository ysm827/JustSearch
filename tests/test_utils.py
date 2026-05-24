"""
Basic tests for JustSearch utility functions.
Run with: python -m pytest tests/ -v
"""

import pytest

from backend.app.llm_client import LLMClient
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

        async def fake_call(*args, **kwargs):
            return '{"type": "search", "queries": ["URLSearchParams delete MDN"]}'

        client._call_with_retry = fake_call

        import asyncio
        result = asyncio.run(client.analyze_task("what does URLSearchParams.delete do"))

        assert result == {
            "type": "search",
            "queries": ["URLSearchParams delete MDN"],
        }

    def test_assess_relevance_accepts_plain_string_response(self):
        client = LLMClient(api_key="test-key", base_url="https://example.test/v1")

        async def fake_call(*args, **kwargs):
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
