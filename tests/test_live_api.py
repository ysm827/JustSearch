import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI


def _live_config():
    api_key = os.getenv("JUSTSEARCH_LIVE_API_KEY", "").strip()
    base_url = os.getenv("JUSTSEARCH_LIVE_BASE_URL", "").strip()
    model = os.getenv("JUSTSEARCH_LIVE_MODEL", "").strip()

    if not api_key or not base_url or not model:
        pytest.skip(
            "set JUSTSEARCH_LIVE_API_KEY, JUSTSEARCH_LIVE_BASE_URL, "
            "and JUSTSEARCH_LIVE_MODEL to run live API tests"
        )
    return api_key, base_url, model


def test_live_settings_validate_key_endpoint():
    api_key, base_url, model = _live_config()

    from backend.app.routers.settings import router

    app = FastAPI()
    app.include_router(router)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/settings/validate-key",
                json={
                    "api_key": api_key,
                    "base_url": base_url,
                    "model_id": model,
                },
                timeout=30,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True, data
        assert data["model"] == model

    asyncio.run(run())


def test_live_llm_client_generates_answer_from_project_source_path():
    api_key, base_url, model = _live_config()

    from backend.app.llm_client import LLMClient

    marker = "JS-LIVE-OK-2026"

    async def run():
        chunks = []
        client = LLMClient(api_key=api_key, base_url=base_url, model=model)
        result = await client.generate_answer(
            "What is the integration smoke-test marker?",
            [
                {
                    "id": 1,
                    "title": "Live API Smoke Test Fixture",
                    "url": "https://example.test/justsearch-live",
                    "content": (
                        "This fixture is only for JustSearch live API testing. "
                        f"The integration smoke-test marker is {marker}."
                    ),
                }
            ],
            stream_callback=chunks.append,
        )

        assert result["status"] == "sufficient", result
        assert marker in result["answer"], result["answer"]
        assert chunks, "expected streaming chunks from the live model"

    asyncio.run(run())


def test_live_llm_client_parses_non_streaming_gateway_sse_responses():
    api_key, base_url, model = _live_config()

    from backend.app.llm_client import LLMClient

    async def run():
        client = LLMClient(api_key=api_key, base_url=base_url, model=model)
        analysis = await client.analyze_task(
            "According to MDN, what does URLSearchParams.delete() do?"
        )
        relevance = await client.assess_relevance(
            "FastAPI CORSMiddleware allow_origins",
            [
                {
                    "id": 1,
                    "title": "FastAPI CORS documentation",
                    "snippet": "CORSMiddleware allow_origins controls allowed origins.",
                },
                {
                    "id": 2,
                    "title": "Random Python tutorial",
                    "snippet": "Python installation guide.",
                },
                {
                    "id": 3,
                    "title": "FastAPI middleware guide",
                    "snippet": "CORS and allow_origins example.",
                },
            ],
        )

        assert analysis["type"] == "search", analysis
        assert analysis["queries"] != [
            "According to MDN, what does URLSearchParams.delete() do?"
        ], analysis
        assert set(relevance) & {1, 3}, relevance
        assert 2 not in relevance, relevance

    asyncio.run(run())
