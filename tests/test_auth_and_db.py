import asyncio
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


def test_context_user_data_dir_is_stable_for_slot(tmp_path):
    from backend.app.browser_context import get_context_user_data_dir

    project_root = Path(tmp_path)
    expected = project_root / "user_data" / "ctx_3"
    assert get_context_user_data_dir(project_root, 3) == expected
