"""
Basic tests for JustSearch utility functions.
Run with: python -m pytest tests/ -v
"""

import pytest

from backend.app.llm_client import _smart_truncate
from backend.app.llm_client import LLMClient


class TestSmartTruncate:
    def test_short_text_unchanged(self):
        text = "Hello world"
        assert _smart_truncate(text, max_chars=100) == text

    def test_empty_text(self):
        assert _smart_truncate("", max_chars=100) == ""
        assert _smart_truncate(None, max_chars=100) == ""

    def test_truncation_at_paragraph(self):
        text = "A" * 5000 + "\n\n" + "B" * 5000
        result = _smart_truncate(text, max_chars=6000)
        assert len(result) < 6100
        assert "[... 内容已截取]" in result

    def test_truncation_at_sentence(self):
        text = "这是一段很长的中文文本。" * 1000
        result = _smart_truncate(text, max_chars=5000)
        assert len(result) < 5100
        assert "[... 内容已截取]" in result

    def test_chinese_semicolon_boundary(self):
        text = "第一部分内容；第二部分内容" * 500
        result = _smart_truncate(text, max_chars=3000)
        assert "[... 内容已截取]" in result


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
