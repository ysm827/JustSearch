"""
Simple in-memory rate limiter for API endpoints.
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter. Thread-safe via GIL for single-process use."""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[str, list] = defaultdict(list)

    def check(self, key: str) -> Tuple[bool, int]:
        """Check if a request is allowed. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old entries
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

        if len(self._requests[key]) >= self.max_requests:
            oldest = self._requests[key][0]
            retry_after = int(oldest + self.window_seconds - now) + 1
            return False, max(retry_after, 1)

        self._requests[key].append(now)
        return True, 0

    def cleanup(self):
        """Remove all expired entries."""
        now = time.time()
        cutoff = now - self.window_seconds
        for key in list(self._requests.keys()):
            self._requests[key] = [t for t in self._requests[key] if t > cutoff]
            if not self._requests[key]:
                del self._requests[key]


# Global rate limiter instance
# 30 requests per minute per IP for chat endpoint
chat_limiter = RateLimiter(max_requests=30, window_seconds=60)
