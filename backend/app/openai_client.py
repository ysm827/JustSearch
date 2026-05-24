"""OpenAI SDK client construction for JustSearch."""

from openai import AsyncOpenAI

from .version import __version__


OPENAI_USER_AGENT = f"JustSearch/{__version__}"
LOCAL_PROVIDER_API_KEY = "justsearch-local-provider"


def create_openai_client(
    api_key: str,
    base_url: str,
    *,
    timeout: float | None = None,
    max_retries: int = 2,
) -> AsyncOpenAI:
    """Create an AsyncOpenAI client with project-level defaults."""
    return AsyncOpenAI(
        api_key=api_key or LOCAL_PROVIDER_API_KEY,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        default_headers={"User-Agent": OPENAI_USER_AGENT},
    )
