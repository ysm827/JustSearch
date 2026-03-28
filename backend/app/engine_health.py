"""
Search engine health monitor.
Tracks failure rates per engine and suggests fallbacks.
"""

import time
import logging
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)


class EngineHealthMonitor:
    """Track search engine success/failure rates for auto-fallback."""

    def __init__(self, window_seconds: int = 300, max_failures: int = 5):
        self.window_seconds = window_seconds
        self.max_failures = max_failures
        self._results: Dict[str, list] = defaultdict(list)

    def record(self, engine: str, success: bool):
        """Record a search result."""
        now = time.time()
        self._results[engine].append((now, success))
        # Prune old entries
        cutoff = now - self.window_seconds
        self._results[engine] = [
            (t, s) for t, s in self._results[engine] if t > cutoff
        ]

    def is_healthy(self, engine: str) -> bool:
        """Check if an engine is healthy enough to use."""
        results = self._results.get(engine, [])
        if not results:
            return True
        recent_failures = sum(1 for _, s in results if not s)
        return recent_failures < self.max_failures

    def get_fallback(self, preferred: str, available: List[str]) -> str:
        """Get the best available engine, falling back if preferred is unhealthy."""
        if self.is_healthy(preferred):
            return preferred

        for engine in available:
            if engine != preferred and self.is_healthy(engine):
                logger.warning(
                    "Engine %s unhealthy, falling back to %s",
                    preferred, engine,
                )
                return engine

        # All unhealthy — try preferred anyway
        logger.warning("All engines unhealthy, using %s anyway", preferred)
        return preferred

    def get_stats(self) -> dict:
        """Return health stats for all engines."""
        stats = {}
        for engine, results in self._results.items():
            total = len(results)
            failures = sum(1 for _, s in results if not s)
            stats[engine] = {
                "total": total,
                "failures": failures,
                "healthy": self.is_healthy(engine),
            }
        return stats


# Global instance
engine_health = EngineHealthMonitor()
