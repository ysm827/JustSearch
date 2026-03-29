"""
Custom exception classes for JustSearch.
Provides structured error handling across all modules.
"""


class JustSearchError(Exception):
    """Base exception for all JustSearch errors."""

    def __init__(self, message: str, code: str = "UNKNOWN"):
        self.message = message
        self.code = code
        super().__init__(message)


class SearchError(JustSearchError):
    """Error during web search operations."""

    def __init__(self, message: str, engine: str = ""):
        self.engine = engine
        super().__init__(message, code="SEARCH_ERROR")


class CrawlError(JustSearchError):
    """Error during page crawling."""

    def __init__(self, message: str, url: str = "", retryable: bool = False):
        self.url = url
        self.retryable = retryable
        super().__init__(message, code="CRAWL_ERROR")


class LLMError(JustSearchError):
    """Error during LLM API calls."""

    def __init__(self, message: str, status_code: int = 0):
        self.status_code = status_code
        super().__init__(message, code="LLM_ERROR")
