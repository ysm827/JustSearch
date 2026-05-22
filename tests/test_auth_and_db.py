import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text


def test_access_control_rejects_remote_api_without_token():
    from backend.app.auth import AccessControlMiddleware

    app = FastAPI()
    app.add_middleware(AccessControlMiddleware, token_provider=lambda: "secret-token")

    @app.get("/api/ping")
    async def ping():
        return JSONResponse({"ok": True})

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/ping")
        assert response.status_code == 401

    asyncio.run(run())


def test_access_control_allows_loopback_without_token():
    from backend.app.auth import AccessControlMiddleware

    app = FastAPI()
    app.add_middleware(AccessControlMiddleware, token_provider=lambda: "secret-token")

    @app.get("/api/ping")
    async def ping():
        return JSONResponse({"ok": True})

    async def run():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/ping")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    asyncio.run(run())


def test_access_control_allows_remote_api_with_bearer_token():
    from backend.app.auth import AccessControlMiddleware

    app = FastAPI()
    app.add_middleware(AccessControlMiddleware, token_provider=lambda: "secret-token")

    @app.get("/api/ping")
    async def ping():
        return JSONResponse({"ok": True})

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/ping",
                headers={"Authorization": "Bearer secret-token"},
            )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    asyncio.run(run())


def test_access_control_rejects_loopback_client_with_untrusted_origin():
    from backend.app.auth import AccessControlMiddleware

    app = FastAPI()
    app.add_middleware(AccessControlMiddleware, token_provider=lambda: "secret-token")

    @app.get("/api/ping")
    async def ping():
        return JSONResponse({"ok": True})

    async def run():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/ping",
                headers={"Origin": "https://evil.example"},
            )
        assert response.status_code == 401

    asyncio.run(run())


def test_access_control_allows_remote_api_with_query_token():
    from backend.app.auth import AccessControlMiddleware

    app = FastAPI()
    app.add_middleware(AccessControlMiddleware, token_provider=lambda: "secret-token")

    @app.get("/api/ping")
    async def ping():
        return JSONResponse({"ok": True})

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/ping?token=secret-token")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    asyncio.run(run())


def test_default_model_settings_use_deepseek_defaults():
    from backend.app.database import DEFAULT_SETTINGS

    example_settings = json.loads(
        Path("backend/settings.json.example").read_text(encoding="utf-8")
    )

    expected = {
        "base_url": "https://api.deepseek.com/v1",
        "model_id": "deepseek-v4-pro",
    }

    assert {key: DEFAULT_SETTINGS[key] for key in expected} == expected
    assert {key: example_settings[key] for key in expected} == expected


def test_save_chat_history_populates_fts_table(tmp_path):
    from backend.app import database

    async def run():
        if database._engine is not None:
            await database._engine.dispose()

        db_path = tmp_path / "justsearch.db"
        database._engine = None
        database._async_session_factory = None
        database._DB_PATH = str(db_path)
        database._DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
        database._CHATS_DIR = str(tmp_path / "legacy_chats")
        database._SETTINGS_FILE = str(tmp_path / "settings.json")

        await database.init_db()
        await database.save_chat_history(
            "session-1",
            [
                {"role": "user", "content": "alpha keyword"},
                {"role": "assistant", "content": "beta summary"},
            ],
            title="FTS Test",
        )

        async with await database.get_session() as session:
            total = (
                await session.execute(text("SELECT count(*) FROM chat_messages_fts"))
            ).scalar_one()
            matched = (
                await session.execute(
                    text("SELECT count(*) FROM chat_messages_fts WHERE chat_messages_fts MATCH 'alpha'")
                )
            ).scalar_one()

        assert total == 2
        assert matched == 1

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_groups_can_manage_sessions(tmp_path):
    from backend.app import database

    async def run():
        if database._engine is not None:
            await database._engine.dispose()

        db_path = tmp_path / "justsearch.db"
        database._engine = None
        database._async_session_factory = None
        database._DB_PATH = str(db_path)
        database._DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
        database._CHATS_DIR = str(tmp_path / "legacy_chats")
        database._SETTINGS_FILE = str(tmp_path / "settings.json")

        await database.init_db()
        await database.save_chat_history(
            "session-1",
            [{"role": "user", "content": "alpha"}],
            title="Alpha",
        )

        group = await database.create_chat_group("研究资料")
        assert group["title"] == "研究资料"
        assert group["is_expanded"] is True

        moved = await database.move_chat_to_group("session-1", group["id"])
        assert moved is True
        chats = await database.list_chats()
        assert chats[0]["group_id"] == group["id"]

        renamed = await database.update_chat_group(
            group["id"],
            title="论文资料",
            is_expanded=False,
        )
        assert renamed["title"] == "论文资料"
        assert renamed["is_expanded"] is False

        deleted = await database.delete_chat_group(group["id"])
        assert deleted is True
        assert await database.list_chat_groups() == []
        chats = await database.list_chats()
        assert chats[0]["group_id"] is None

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_context_user_data_dir_is_stable_for_slot(tmp_path):
    from backend.app.browser_context import get_context_user_data_dir

    project_root = Path(tmp_path)
    expected = project_root / "user_data" / "ctx_3"
    assert get_context_user_data_dir(project_root, 3) == expected


def test_search_rate_limit_releases_lock_before_context_body():
    from backend.app import browser_context

    async def run():
        original_interval = browser_context._MIN_SEARCH_INTERVAL
        original_last_request_time = browser_context._LAST_REQUEST_TIME

        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()
        first = None
        second = None

        async def first_task():
            async with browser_context.search_rate_limit():
                first_entered.set()
                await release_first.wait()

        async def second_task():
            await first_entered.wait()
            async with browser_context.search_rate_limit():
                second_entered.set()

        try:
            browser_context._MIN_SEARCH_INTERVAL = 0
            browser_context._LAST_REQUEST_TIME = 0

            first = asyncio.create_task(first_task())
            second = asyncio.create_task(second_task())

            await asyncio.wait_for(first_entered.wait(), timeout=1)
            await asyncio.sleep(0)
            await asyncio.wait_for(second_entered.wait(), timeout=1)

            release_first.set()
            await asyncio.gather(first, second)
        finally:
            release_first.set()
            for task in (first, second):
                if task and not task.done():
                    task.cancel()
            tasks = [task for task in (first, second) if task]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            browser_context._MIN_SEARCH_INTERVAL = original_interval
            browser_context._LAST_REQUEST_TIME = original_last_request_time

    asyncio.run(run())


def test_validate_key_resolves_masked_key(tmp_path):
    from unittest.mock import AsyncMock, patch
    from backend.app import database
    from backend.app.routers.settings import router

    async def run():
        if database._engine is not None:
            await database._engine.dispose()
        db_path = tmp_path / "justsearch.db"
        database._engine = None
        database._async_session_factory = None
        database._DB_PATH = str(db_path)
        database._DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
        database._CHATS_DIR = str(tmp_path / "legacy_chats")
        database._SETTINGS_FILE = str(tmp_path / "settings.json")
        await database.init_db()

        # Save actual key
        await database.save_settings({"api_key": "actual-secret-key-999"})

        # Mock openai client call
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock()

        app = FastAPI()
        app.include_router(router)

        with patch("backend.app.routers.settings.create_openai_client", return_value=mock_client) as mock_create:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/settings/validate-key",
                    json={
                        "api_key": "act****-999",  # masked key
                        "base_url": "https://api.example.com",
                        "model_id": "test-model"
                    }
                )
            
            assert response.status_code == 200
            data = response.json()
            assert data["valid"] is True
            mock_create.assert_called_once_with(api_key="actual-secret-key-999", base_url="https://api.example.com")

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_check_search_engines_reports_availability(monkeypatch):
    from backend.app.routers.settings import router

    calls = []

    class FakeBrowserManager:
        def __init__(self, engine, max_results):
            self.engine = engine
            self.max_results = max_results

        async def search_web(
            self,
            query,
            log_func=None,
            session_id=None,
            allow_fallback=True,
            use_cache=True,
        ):
            calls.append(
                {
                    "engine": self.engine,
                    "max_results": self.max_results,
                    "query": query,
                    "allow_fallback": allow_fallback,
                    "use_cache": use_cache,
                }
            )
            if self.engine == "bing":
                return []
            return [{"title": "JustSearch", "url": "https://example.com"}]

    monkeypatch.setattr(
        "backend.app.routers.settings.BrowserManager",
        FakeBrowserManager,
    )
    monkeypatch.setattr(
        "backend.app.routers.settings.get_all_engines",
        lambda: ["duckduckgo", "bing"],
    )

    app = FastAPI()
    app.include_router(router)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/settings/check-engines")

        assert response.status_code == 200
        assert response.json() == {
            "query": "JustSearch test",
            "results": [
                {
                    "engine": "duckduckgo",
                    "available": True,
                    "result_count": 1,
                    "error": "",
                },
                {
                    "engine": "bing",
                    "available": False,
                    "result_count": 0,
                    "error": "未解析到搜索结果",
                },
            ],
        }
        assert calls == [
            {
                "engine": "duckduckgo",
                "max_results": 3,
                "query": "JustSearch test",
                "allow_fallback": False,
                "use_cache": False,
            },
            {
                "engine": "bing",
                "max_results": 3,
                "query": "JustSearch test",
                "allow_fallback": False,
                "use_cache": False,
            },
        ]

    asyncio.run(run())
