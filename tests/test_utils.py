"""
Basic tests for JustSearch utility functions.
Run with: python -m pytest tests/ -v
"""

import pytest


def _smart_truncate(text: str, max_chars: int = 8000) -> str:
    """Inline copy for testing without project dependencies."""
    if not text or len(text) <= max_chars:
        return text or ""
    truncated = text[:max_chars]
    last_paragraph = truncated.rfind('\n\n')
    if last_paragraph > max_chars * 0.5:
        return truncated[:last_paragraph].rstrip() + "\n\n[... 内容已截取]"
    last_newline = truncated.rfind('\n')
    if last_newline > max_chars * 0.5:
        return truncated[:last_newline].rstrip() + "\n[... 内容已截取]"
    last_sentence = max(
        truncated.rfind('。'), truncated.rfind('.'),
        truncated.rfind('！'), truncated.rfind('!'),
        truncated.rfind('？'), truncated.rfind('?'),
        truncated.rfind('；'), truncated.rfind(';'),
    )
    if last_sentence > max_chars * 0.5:
        return truncated[:last_sentence + 1] + "[... 内容已截取]"
    return truncated + "[... 内容已截取]"


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
