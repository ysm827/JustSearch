"""
Search engine health monitor.
Tracks failure rates per engine and suggests fallbacks.
"""

import time
import logging
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)

_FALLBACK_PRIORITY = [
    "bing",
    "sogou",
    "brave",
    "google",
    "searxng",
    "duckduckgo",
]


class EngineHealthMonitor:
    """Track search engine success/failure rates for auto-fallback."""

    _FAILURE_WEIGHTS = {
        "batch_soft_timeout": 0.5,
        "selector": 1.0,
        "low_quality": 1.0,
        "timeout": 2.0,
        "blocked": 3.0,
        "captcha": 3.0,
        "other": 2.0,
    }
    _CRITICAL_FAILURE_REASONS = {"timeout", "blocked", "captcha", "other"}
    _STREAK_BREAKING_REASONS = {"batch_soft_timeout"}

    def __init__(
        self,
        window_seconds: int = 300,
        max_failure_score: float = 6.0,
        max_failure_streak: int = 3,
        max_critical_failure_streak: int = 3,
    ):
        self.window_seconds = window_seconds
        self.max_failure_score = max_failure_score
        self.max_failure_streak = max_failure_streak
        self.max_critical_failure_streak = max_critical_failure_streak
        self._results: Dict[str, list] = defaultdict(list)

    def record(
        self,
        engine: str,
        success: bool,
        reason: str = "",
        batch_id: str | None = None,
    ):
        """Record a search result."""
        now = time.time()
        normalized_reason = self._normalize_reason(reason, success)
        self._results[engine].append((now, success, normalized_reason, batch_id))
        self._prune(engine, now)

    def _normalize_reason(self, reason: str, success: bool) -> str:
        if success:
            return ""
        normalized = (reason or "other").strip().lower()
        if normalized not in self._FAILURE_WEIGHTS:
            return "other"
        return normalized

    def _prune(self, engine: str, now: float | None = None):
        # Prune old entries
        now = now or time.time()
        cutoff = now - self.window_seconds
        self._results[engine] = [
            entry for entry in self._results[engine] if entry[0] > cutoff
        ]

    def _effective_results(self, results: list) -> list:
        successful_batches = {
            batch_id
            for _, success, _, batch_id in results
            if batch_id is not None and success
        }
        effective = []
        for timestamp, success, reason, batch_id in results:
            effective_reason = reason
            if (
                not success
                and reason == "timeout"
                and batch_id in successful_batches
            ):
                effective_reason = "batch_soft_timeout"
            effective.append((timestamp, success, effective_reason, batch_id))
        return effective

    def _stats_for(self, engine: str) -> dict:
        self._prune(engine)
        results = self._effective_results(self._results.get(engine, []))
        total = len(results)
        failures = sum(1 for _, success, _, _ in results if not success)
        failure_score = sum(
            self._FAILURE_WEIGHTS.get(reason, self._FAILURE_WEIGHTS["other"])
            for _, success, reason, _ in results
            if not success
        )
        failure_reasons: Dict[str, int] = defaultdict(int)
        for _, success, reason, _ in results:
            if not success:
                failure_reasons[reason] += 1

        failure_streak = 0
        critical_failure_streak = 0
        for _, success, reason, _ in reversed(results):
            if success or reason in self._STREAK_BREAKING_REASONS:
                break
            failure_streak += 1
            if reason in self._CRITICAL_FAILURE_REASONS:
                critical_failure_streak += 1

        healthy = (
            failure_score < self.max_failure_score
            and failure_streak < self.max_failure_streak
            and critical_failure_streak < self.max_critical_failure_streak
        )
        return {
            "total": total,
            "failures": failures,
            "failure_score": failure_score,
            "failure_reasons": dict(failure_reasons),
            "failure_streak": failure_streak,
            "critical_failure_streak": critical_failure_streak,
            "healthy": healthy,
        }

    def is_healthy(self, engine: str) -> bool:
        """Check if an engine is healthy enough to use."""
        return self._stats_for(engine)["healthy"]

    def get_fallback(self, preferred: str, available: List[str]) -> str:
        """Get the best available engine, falling back if preferred is unhealthy."""
        if self.is_healthy(preferred):
            return preferred

        priority = [
            engine for engine in _FALLBACK_PRIORITY
            if engine in available and engine != preferred
        ]
        priority.extend(
            engine for engine in available
            if engine not in priority and engine != preferred
        )

        for engine in priority:
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
        for engine in self._results:
            stats[engine] = self._stats_for(engine)
        return stats

    def get_latest_failure_reason(
        self,
        engine: str,
        since: float | None = None,
    ) -> str:
        """Return the latest failure reason for an engine within the active window."""
        self._prune(engine)
        results = self._effective_results(self._results.get(engine, []))
        for timestamp, success, reason, _ in reversed(results):
            if since is not None and timestamp < since:
                break
            if success:
                return ""
            return reason
        return ""


# Global instance
engine_health = EngineHealthMonitor()
