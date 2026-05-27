import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
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


def test_html_bootstrap_includes_token_for_loopback_client(monkeypatch):
    from backend.app import auth

    monkeypatch.setenv("JUSTSEARCH_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth, "get_auth_token", lambda: "secret-token")

    app = FastAPI()

    @app.get("/")
    async def index(request: Request):
        return JSONResponse(auth.build_html_bootstrap_payload(request))

    async def run():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert response.json() == {
            "authEnabled": True,
            "clientIsLoopback": True,
            "authToken": "secret-token",
        }

    asyncio.run(run())


def test_html_bootstrap_omits_token_when_remote_client_spoofs_localhost_host(monkeypatch):
    from backend.app import auth

    monkeypatch.setenv("JUSTSEARCH_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth, "get_auth_token", lambda: "secret-token")

    app = FastAPI()

    @app.get("/")
    async def index(request: Request):
        return JSONResponse(auth.build_html_bootstrap_payload(request))

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert response.json() == {
            "authEnabled": True,
            "clientIsLoopback": False,
        }

    asyncio.run(run())


def test_html_bootstrap_omits_token_for_remote_page_host(monkeypatch):
    from backend.app import auth

    monkeypatch.setenv("JUSTSEARCH_AUTH_ENABLED", "true")
    monkeypatch.setattr(auth, "get_auth_token", lambda: "secret-token")

    app = FastAPI()

    @app.get("/")
    async def index(request: Request):
        return JSONResponse(auth.build_html_bootstrap_payload(request))

    async def run():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://example.com") as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert response.json() == {
            "authEnabled": True,
            "clientIsLoopback": False,
        }

    asyncio.run(run())


def test_default_model_settings_use_deepseek_defaults():
    from backend.app.database import DEFAULT_SETTINGS

    example_settings = json.loads(
        Path("backend/settings.json.example").read_text(encoding="utf-8")
    )

    assert DEFAULT_SETTINGS["default_provider_id"] == "deepseek"
    assert DEFAULT_SETTINGS["providers"][0]["id"] == "deepseek"
    assert DEFAULT_SETTINGS["providers"][0]["base_url"] == "https://api.deepseek.com/v1"
    assert DEFAULT_SETTINGS["providers"][0]["model_id"] == "deepseek-v4-pro"
    assert set(DEFAULT_SETTINGS["workflow_step_models"]) == {
        "analysis",
        "relevance",
        "interaction",
        "answer",
    }
    assert DEFAULT_SETTINGS["search_engine"] == "searxng"
    assert DEFAULT_SETTINGS["live_artifacts_mode"] is False
    assert example_settings["default_provider_id"] == "deepseek"
    assert example_settings["providers"][0]["id"] == "deepseek"
    assert example_settings["providers"][0]["base_url"] == "https://api.deepseek.com/v1"
    assert example_settings["providers"][0]["model_id"] == "deepseek-v4-pro"
    assert set(example_settings["workflow_step_models"]) == {
        "analysis",
        "relevance",
        "interaction",
        "answer",
    }
    assert example_settings["search_engine"] == "searxng"
    assert example_settings["live_artifacts_mode"] is False


def test_chat_router_coerces_string_booleans_for_live_artifacts():
    from backend.app.routers.chat import _coerce_bool

    assert _coerce_bool("false") is False
    assert _coerce_bool("0") is False
    assert _coerce_bool("true") is True
    assert _coerce_bool("1") is True
    assert _coerce_bool(None, default=True) is True


def test_markdown_source_formatter_rejects_unsafe_urls():
    from backend.app.routers.history import _format_source_markdown_item

    assert _format_source_markdown_item(
        {"title": "Unsafe [link]", "url": "javascript:alert(1)"}
    ) == "- Unsafe \\[link\\]"
    assert _format_source_markdown_item(
        {"title": "Safe ] title", "url": "https://example.com/a)b"}
    ) == "- [Safe \\] title](https://example.com/a%29b)"


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


def test_cleanup_old_sessions_preserves_sessions_with_messages(tmp_path):
    from datetime import datetime, timedelta
    from sqlalchemy import select
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
            "old-with-message",
            [{"role": "user", "content": "must survive"}],
            title="Keep",
        )

        old_time = datetime.now() - timedelta(days=120)
        async with await database.get_session() as session:
            sess = (
                await session.execute(
                    select(database.ChatSession).where(database.ChatSession.id == "old-with-message")
                )
            ).scalar_one()
            sess.created_at = old_time
            sess.updated_at = old_time
            session.add(
                database.ChatSession(
                    id="old-empty",
                    title="Empty",
                    created_at=old_time,
                    updated_at=old_time,
                )
            )
            await session.commit()

        await database._cleanup_old_sessions(max_age_days=90)

        assert await database.load_chat_history("old-with-message") is not None
        assert await database.load_chat_history("old-empty") is None

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_load_chat_history_rejects_route_unsafe_ids_without_basename_fallback(tmp_path):
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
            "session",
            [{"role": "user", "content": "must not be returned for bad/session"}],
            title="Real Session",
        )

        assert await database.load_chat_history("bad/session") is None
        assert await database.load_chat_history("also\\bad") is None

        legacy_path = database.get_chat_path("session")
        loaded = await database.load_chat_history(legacy_path)
        assert loaded["id"] == "session"
        assert loaded["title"] == "Real Session"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_legacy_chat_migration_rejects_route_unsafe_ids(tmp_path):
    from backend.app import database

    async def run():
        if database._engine is not None:
            await database._engine.dispose()

        legacy_dir = tmp_path / "legacy_chats"
        legacy_dir.mkdir()
        (legacy_dir / "fallback-session.json").write_text(
            json.dumps(
                {
                    "id": "bad/session",
                    "title": "Fallback Session",
                    "messages": [{"role": "user", "content": "use filename id"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (legacy_dir / "bad\\filename.json").write_text(
            json.dumps(
                {
                    "id": "also\\bad",
                    "title": "Unsafe Session",
                    "messages": [{"role": "user", "content": "skip me"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        db_path = tmp_path / "justsearch.db"
        database._engine = None
        database._async_session_factory = None
        database._DB_PATH = str(db_path)
        database._DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
        database._CHATS_DIR = str(legacy_dir)
        database._SETTINGS_FILE = str(tmp_path / "settings.json")

        await database.init_db()

        fallback = await database.load_chat_history("fallback-session")
        assert fallback["title"] == "Fallback Session"
        assert fallback["messages"][0]["content"] == "use filename id"
        assert await database.load_chat_history("bad/session") is None
        assert await database.load_chat_history("also\\bad") is None
        assert await database.load_chat_history("bad\\filename") is None

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


def test_delete_chat_endpoint_returns_404_for_missing_session(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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
            "delete-me",
            [{"role": "user", "content": "remove this"}],
            title="Delete Me",
        )

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            deleted = await client.delete("/api/history/delete-me")
            missing = await client.delete("/api/history/delete-me")

        assert deleted.status_code == 200
        assert deleted.json() == {"status": "ok"}
        assert await database.load_chat_history("delete-me") is None
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Chat not found"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_rename_chat_endpoint_tolerates_non_string_title_payloads(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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
            "rename-me",
            [{"role": "user", "content": "rename this"}],
            title="Old Title",
        )

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            empty_title = await client.patch("/api/history/rename-me", json={"title": None})
            numeric_title = await client.patch("/api/history/rename-me", json={"title": 12345})

        assert empty_title.status_code == 400
        assert empty_title.json()["detail"] == "Title cannot be empty"
        assert numeric_title.status_code == 200
        assert numeric_title.json() == {"status": "ok", "title": "12345"}
        assert (await database.load_chat_history("rename-me"))["title"] == "12345"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_history_routes_reject_route_unsafe_ids_before_db_operations(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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
            "safe-session",
            [{"role": "user", "content": "safe"}],
            title="Safe Session",
        )
        async with await database.get_session() as session:
            await session.execute(
                text(
                    "INSERT INTO chat_sessions (id, title, created_at, updated_at) "
                    "VALUES ('bad\\session', 'Dirty Session', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO chat_groups (id, title, is_expanded, created_at, updated_at) "
                    "VALUES ('bad\\group', 'Dirty Group', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            await session.commit()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            get_dirty = await client.get("/api/history/bad%5Csession")
            delete_dirty = await client.delete("/api/history/bad%5Csession")
            rename_dirty = await client.patch(
                "/api/history/bad%5Csession",
                json={"title": "Should Not Rename"},
            )
            export_dirty = await client.get("/api/history/bad%5Csession/export")
            update_dirty_group = await client.patch(
                "/api/history/groups/bad%5Cgroup",
                json={"title": "Should Not Rename"},
            )
            move_to_dirty_group = await client.patch(
                "/api/history/safe-session/group",
                json={"group_id": "bad\\group"},
            )

        assert get_dirty.status_code == 400
        assert delete_dirty.status_code == 400
        assert rename_dirty.status_code == 400
        assert export_dirty.status_code == 400
        assert update_dirty_group.status_code == 400
        assert move_to_dirty_group.status_code == 400
        assert get_dirty.json()["detail"] == "session_id 格式无效"
        assert update_dirty_group.json()["detail"] == "group_id 格式无效"
        assert move_to_dirty_group.json()["detail"] == "group_id 格式无效"

        async with await database.get_session() as session:
            dirty_session_title = (
                await session.execute(
                    text("SELECT title FROM chat_sessions WHERE id = 'bad\\session'")
                )
            ).scalar_one()
            dirty_group_title = (
                await session.execute(
                    text("SELECT title FROM chat_groups WHERE id = 'bad\\group'")
                )
            ).scalar_one()
            safe_group_id = (
                await session.execute(
                    text("SELECT group_id FROM chat_sessions WHERE id = 'safe-session'")
                )
            ).scalar_one()

        assert dirty_session_title == "Dirty Session"
        assert dirty_group_title == "Dirty Group"
        assert safe_group_id is None

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_import_history_package_adds_sessions_and_groups_without_overwriting(tmp_path):
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
            "existing-session",
            [{"role": "user", "content": "keep original"}],
            title="Original",
        )

        summary = await database.import_history_package(
            {
                "type": "JustSearch-History",
                "version": 1,
                "groups": [
                    {
                        "id": "group-imported",
                        "title": "Imported Group",
                        "is_expanded": False,
                        "timestamp": "2026-05-22T10:00:00Z",
                    }
                ],
                "history": [
                    {
                        "id": "existing-session",
                        "title": "Should Not Replace",
                        "messages": [{"role": "user", "content": "replace attempt"}],
                    },
                    {
                        "id": "new-session",
                        "title": "Imported Chat",
                        "group_id": "group-imported",
                        "timestamp": "2026-05-22T11:00:00Z",
                        "messages": [
                            {"role": "user", "content": "hello imported"},
                            {
                                "role": "assistant",
                                "content": "answer imported",
                                "sources": [{"title": "Source", "url": "https://example.com"}],
                                "stats": {"sites_searched": 1},
                            },
                        ],
                    },
                ],
            }
        )

        assert summary == {
            "imported_sessions": 1,
            "skipped_sessions": 1,
            "imported_groups": 1,
            "skipped_groups": 0,
        }

        existing = await database.load_chat_history("existing-session")
        imported = await database.load_chat_history("new-session")
        groups = await database.list_chat_groups()

        assert existing["title"] == "Original"
        assert existing["messages"][0]["content"] == "keep original"
        assert imported["title"] == "Imported Chat"
        assert imported["group_id"] == "group-imported"
        assert imported["messages"][1]["sources"] == [{"title": "Source", "url": "https://example.com"}]
        assert imported["messages"][1]["stats"] == {"sites_searched": 1}
        assert groups == [
            {
                "id": "group-imported",
                "title": "Imported Group",
                "is_expanded": False,
                "timestamp": "2026-05-22T10:00:00Z",
            }
        ]

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_import_history_package_ignores_non_scalar_group_id(tmp_path):
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

        summary = await database.import_history_package(
            {
                "type": "JustSearch-History",
                "version": 1,
                "history": [
                    {
                        "id": "bad-group-id",
                        "title": "Bad Group ID",
                        "group_id": ["not", "hashable"],
                        "messages": [{"role": "user", "content": "hello"}],
                    }
                ],
            }
        )

        imported = await database.load_chat_history("bad-group-id")

        assert summary["imported_sessions"] == 1
        assert imported["group_id"] is None

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_import_history_package_ignores_route_unsafe_ids(tmp_path):
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

        summary = await database.import_history_package(
            {
                "type": "JustSearch-History",
                "version": 1,
                "groups": [
                    {"id": "bad/group", "title": "Bad Group"},
                    {"id": "good-group", "title": "Good Group"},
                ],
                "history": [
                    {
                        "id": "bad/session",
                        "title": "Bad Session",
                        "messages": [{"role": "user", "content": "bad"}],
                    },
                    {
                        "id": "also\\bad",
                        "title": "Bad Backslash",
                        "messages": [{"role": "user", "content": "bad"}],
                    },
                    {
                        "id": "good-session",
                        "title": "Good Session",
                        "group_id": "bad/group",
                        "messages": [{"role": "user", "content": "good"}],
                    },
                ],
            }
        )

        imported = await database.load_chat_history("good-session")
        groups = await database.list_chat_groups()

        assert summary == {
            "imported_sessions": 1,
            "skipped_sessions": 0,
            "imported_groups": 1,
            "skipped_groups": 0,
        }
        assert await database.load_chat_history("bad/session") is None
        assert await database.load_chat_history("also\\bad") is None
        assert imported["title"] == "Good Session"
        assert imported["group_id"] is None
        assert [group["id"] for group in groups] == ["good-group"]

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_history_import_endpoint_accepts_json_package(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/history/import",
                json={
                    "type": "JustSearch-History",
                    "version": 1,
                    "history": [
                        {
                            "id": "endpoint-session",
                            "title": "Endpoint Import",
                            "messages": [{"role": "user", "content": "from endpoint"}],
                        }
                    ],
                    "groups": [],
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "imported_sessions": 1,
            "skipped_sessions": 0,
            "imported_groups": 0,
            "skipped_groups": 0,
        }
        assert (await database.load_chat_history("endpoint-session"))["title"] == "Endpoint Import"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_history_search_handles_fts_special_characters(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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
            "fts-special",
            [
                {"role": "user", "content": "explain qwen2.5:7b behavior"},
                {"role": "assistant", "content": "The token alpha-beta appears in content."},
            ],
            title="Tokenizer Edge",
        )

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/history/search", params={"q": "alpha-beta"})

        assert response.status_code == 200
        assert response.json()[0]["id"] == "fts-special"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_history_lists_and_search_hide_route_unsafe_legacy_ids(tmp_path):
    from backend.app import database
    from backend.app.routers.history import router

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
        async with await database.get_session() as session:
            session.add(database.ChatGroup(id="safe-group", title="Safe Group"))
            session.add(database.ChatGroup(id="bad\\group", title="Dirty Group"))
            session.add(
                database.ChatSession(
                    id="visible-session",
                    title="Visible Session",
                    group_id="bad\\group",
                )
            )
            session.add(
                database.ChatMessage(
                    session_id="visible-session",
                    role="user",
                    content="sharedtoken visible",
                )
            )
            session.add(
                database.ChatSession(
                    id="bad\\session",
                    title="Dirty Session",
                )
            )
            session.add(
                database.ChatMessage(
                    session_id="bad\\session",
                    role="user",
                    content="sharedtoken dirty",
                )
            )
            await session.commit()

        chats = await database.list_chats()
        groups = await database.list_chat_groups()

        assert [chat["id"] for chat in chats] == ["visible-session"]
        assert chats[0]["group_id"] is None
        assert [group["id"] for group in groups] == ["safe-group"]

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            search_response = await client.get("/api/history/search", params={"q": "sharedtoken"})

        assert search_response.status_code == 200
        assert [item["id"] for item in search_response.json()] == ["visible-session"]
        assert search_response.json()[0]["group_id"] is None

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


def test_preferred_browser_channel_uses_bundled_chromium_without_system_chrome(monkeypatch):
    from backend.app import browser_context

    monkeypatch.delenv("PLAYWRIGHT_BROWSER_CHANNEL", raising=False)
    monkeypatch.delenv("BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(browser_context, "_system_chrome_available", lambda: False)

    assert browser_context.get_preferred_browser_channel() is None


def test_preferred_browser_channel_uses_system_chrome_when_available(monkeypatch):
    from backend.app import browser_context

    monkeypatch.delenv("PLAYWRIGHT_BROWSER_CHANNEL", raising=False)
    monkeypatch.delenv("BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(browser_context, "_system_chrome_available", lambda: True)

    assert browser_context.get_preferred_browser_channel() == "chrome"


def test_preferred_browser_channel_honors_explicit_browser_channel(monkeypatch):
    from backend.app import browser_context

    monkeypatch.setenv("BROWSER_CHANNEL", "msedge")
    monkeypatch.delenv("PLAYWRIGHT_BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(browser_context, "_system_chrome_available", lambda: False)

    assert browser_context.get_preferred_browser_channel() == "msedge"

    monkeypatch.setenv("PLAYWRIGHT_BROWSER_CHANNEL", "chromium")
    assert browser_context.get_preferred_browser_channel() is None


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

        await database.save_settings(
            {
                "default_provider_id": "openai",
                "providers": [
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "api_key": "actual-secret-key-999",
                        "base_url": "https://api.example.com",
                        "model_id": "test-model",
                    }
                ],
            }
        )

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
                        "provider_id": "openai-renamed",
                        "previous_provider_id": "openai",
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


def test_validate_key_allows_empty_api_key_for_local_provider():
    from unittest.mock import AsyncMock, patch
    from backend.app.routers.settings import router

    async def run():
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
                        "provider_id": "ollama",
                        "api_key": "",
                        "base_url": "http://host.docker.internal:11434/v1",
                        "model_id": "llama3.1",
                    },
                )

            assert response.status_code == 200
            assert response.json()["valid"] is True
            mock_create.assert_called_once_with(
                api_key="",
                base_url="http://host.docker.internal:11434/v1",
            )

    asyncio.run(run())


def test_validate_key_rejects_empty_api_key_for_remote_provider():
    from unittest.mock import patch
    from backend.app.routers.settings import router

    async def run():
        app = FastAPI()
        app.include_router(router)

        with patch("backend.app.routers.settings.create_openai_client") as mock_create:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/settings/validate-key",
                    json={
                        "provider_id": "deepseek",
                        "api_key": "",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-v4-pro",
                    },
                )

            assert response.status_code == 200
            assert response.json() == {
                "valid": False,
                "error": "请先填写 API 密钥",
            }
            mock_create.assert_not_called()

    asyncio.run(run())


def test_validate_key_tolerates_non_string_request_fields():
    from unittest.mock import patch
    from backend.app.routers.settings import router

    async def run():
        app = FastAPI()
        app.include_router(router)

        with patch("backend.app.routers.settings.create_openai_client") as mock_create:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/settings/validate-key",
                    json={
                        "provider_id": 123,
                        "previous_provider_id": None,
                        "api_key": None,
                        "base_url": None,
                        "model_id": 456,
                    },
                )

        assert response.status_code == 200
        assert response.json() == {
            "valid": False,
            "error": "请先填写 API 密钥",
        }
        mock_create.assert_not_called()

    asyncio.run(run())


def test_validate_key_rejects_gemini_25_models():
    from unittest.mock import patch
    from backend.app.routers.settings import router

    async def run():
        app = FastAPI()
        app.include_router(router)

        with patch("backend.app.routers.settings.create_openai_client") as mock_create:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/settings/validate-key",
                    json={
                        "provider_id": "gemini",
                        "api_key": "token",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                        "model_id": "gemini-2.5-pro",
                    },
                )

            assert response.status_code == 200
            assert response.json() == {
                "valid": False,
                "error": "Gemini 2.5 系列模型不再支持",
            }
            mock_create.assert_not_called()

    asyncio.run(run())


def test_validate_key_reports_provider_subscription_failure():
    from unittest.mock import AsyncMock, patch
    from backend.app.routers.settings import router

    async def run():
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception(
                "Error code: 403 - {'code': 'SUBSCRIPTION_NOT_FOUND', "
                "'message': 'No active subscription found for this group'}"
            )
        )

        app = FastAPI()
        app.include_router(router)

        with patch("backend.app.routers.settings.create_openai_client", return_value=mock_client):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.post(
                    "/api/settings/validate-key",
                    json={
                        "provider_id": "deepseek",
                        "api_key": "sk-test",
                        "base_url": "https://inferaichat.com/v1",
                        "model_id": "deepseek-v4-pro",
                    },
                )

        assert response.status_code == 200
        assert response.json() == {
            "valid": False,
            "error": "模型服务返回 403：当前 API Key 所属账户没有可用订阅，请在模型服务后台开通/续订后重试。",
        }

    asyncio.run(run())


def test_settings_api_saves_multiple_providers_and_masks_keys(tmp_path):
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

        app = FastAPI()
        app.include_router(router)

        payload = {
            "theme": "dark",
            "default_provider_id": "openai",
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "api_key": "deepseek-secret-1234",
                    "base_url": "https://api.deepseek.com/v1",
                    "model_id": "deepseek-chat",
                },
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "api_key": "openai-secret-5678",
                    "base_url": "https://api.openai.com/v1",
                    "model_id": "gpt-4.1, gpt-4.1-mini",
                },
            ],
            "workflow_step_models": {
                "analysis": {"provider_id": "deepseek", "model_id": "deepseek-chat"},
                "relevance": {"provider_id": "openai", "model_id": "gpt-4.1-mini"},
                "interaction": {"provider_id": "", "model_id": ""},
                "answer": {"provider_id": "openai", "model_id": "gpt-4.1"},
            },
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            saved_response = await client.post("/api/settings", json=payload)
            loaded_response = await client.get("/api/settings")

        assert saved_response.status_code == 200
        saved = saved_response.json()["settings"]
        loaded = loaded_response.json()
        assert saved["default_provider_id"] == "openai"
        assert saved["api_key"] == "ope****5678"
        assert saved["base_url"] == "https://api.openai.com/v1"
        assert saved["model_id"] == "gpt-4.1, gpt-4.1-mini"
        assert loaded["default_provider_id"] == "openai"
        assert loaded["api_key"] == "ope****5678"
        assert [provider["id"] for provider in loaded["providers"]] == ["deepseek", "openai"]
        assert loaded["providers"][0]["api_key"] == "dee****1234"
        assert loaded["providers"][1]["api_key"] == "ope****5678"
        assert loaded["workflow_step_models"]["analysis"] == {
            "provider_id": "deepseek",
            "model_id": "deepseek-chat",
        }
        assert loaded["workflow_step_models"]["relevance"] == {
            "provider_id": "openai",
            "model_id": "gpt-4.1-mini",
        }
        assert loaded["workflow_step_models"]["interaction"] == {
            "provider_id": "",
            "model_id": "",
        }
        assert loaded["workflow_step_models"]["answer"] == {
            "provider_id": "openai",
            "model_id": "gpt-4.1",
        }

        raw_settings = await database.load_settings()
        assert raw_settings["api_key"] == "openai-secret-5678"
        assert raw_settings["base_url"] == "https://api.openai.com/v1"
        assert raw_settings["model_id"] == "gpt-4.1, gpt-4.1-mini"
        assert raw_settings["providers"][0]["api_key"] == "deepseek-secret-1234"
        assert raw_settings["providers"][1]["api_key"] == "openai-secret-5678"
        assert raw_settings["workflow_step_models"]["answer"]["model_id"] == "gpt-4.1"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_settings_api_partial_update_preserves_existing_values(tmp_path):
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
        await database.save_settings(
            {
                "theme": "light",
                "search_engine": "searxng",
                "max_results": 17,
                "max_iterations": 4,
                "interactive_search": False,
                "max_concurrent_pages": 6,
            }
        )

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/settings", json={"theme": "dark"})
            loaded_response = await client.get("/api/settings")

        assert response.status_code == 200
        saved = response.json()["settings"]
        loaded = loaded_response.json()
        assert saved["theme"] == "dark"
        assert saved["search_engine"] == "searxng"
        assert saved["max_results"] == 17
        assert saved["max_iterations"] == 4
        assert saved["interactive_search"] is False
        assert saved["max_concurrent_pages"] == 6
        assert loaded["search_engine"] == "searxng"

        raw_settings = await database.load_settings()
        assert raw_settings["theme"] == "dark"
        assert raw_settings["search_engine"] == "searxng"
        assert raw_settings["max_results"] == 17
        assert raw_settings["max_iterations"] == 4
        assert raw_settings["interactive_search"] is False
        assert raw_settings["max_concurrent_pages"] == 6

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_settings_api_uses_configured_search_engines(tmp_path, monkeypatch):
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

        app = FastAPI()
        app.include_router(router)
        monkeypatch.setattr(
            "backend.app.routers.settings.get_all_engines",
            lambda: ["searxng", "custom-engine"],
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            saved_response = await client.post(
                "/api/settings",
                json={"search_engine": "custom-engine"},
            )
            rejected_response = await client.post(
                "/api/settings",
                json={"search_engine": "missing-engine"},
            )

        assert saved_response.status_code == 200
        assert saved_response.json()["settings"]["search_engine"] == "custom-engine"
        assert rejected_response.status_code == 400
        assert "custom-engine" in rejected_response.json()["detail"]
        assert "missing-engine" not in rejected_response.json()["detail"]

        raw_settings = await database.load_settings()
        assert raw_settings["search_engine"] == "custom-engine"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_settings_api_preserves_masked_provider_key_when_id_changes(tmp_path):
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
        await database.save_settings(
            {
                "default_provider_id": "openai",
                "providers": [
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "api_key": "openai-secret-1234",
                        "base_url": "https://api.openai.com/v1",
                        "model_id": "gpt-4.1",
                    }
                ],
            }
        )

        app = FastAPI()
        app.include_router(router)

        payload = {
            "default_provider_id": "openai-renamed",
            "providers": [
                {
                    "id": "openai-renamed",
                    "previous_id": "openai",
                    "name": "OpenAI",
                    "api_key": "ope****1234",
                    "base_url": "https://api.openai.com/v1",
                    "model_id": "gpt-4.1",
                }
            ],
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/settings", json=payload)
            loaded_response = await client.get("/api/settings")

        assert response.status_code == 200
        saved = response.json()["settings"]
        loaded = loaded_response.json()
        assert saved["default_provider_id"] == "openai-renamed"
        assert saved["providers"][0]["api_key"] == "ope****1234"
        assert loaded["providers"][0]["id"] == "openai-renamed"
        assert loaded["providers"][0]["api_key"] == "ope****1234"

        raw_settings = await database.load_settings()
        assert raw_settings["api_key"] == "openai-secret-1234"
        assert raw_settings["providers"][0]["api_key"] == "openai-secret-1234"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_settings_api_default_provider_switch_updates_primary_legacy_fields(tmp_path):
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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "api_key": "deepseek-secret",
                "base_url": "https://api.deepseek.com/v1",
                "model_id": "deepseek-chat",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "api_key": "openai-secret",
                        "base_url": "https://api.openai.com/v1",
                        "model_id": "gpt-4.1",
                    },
                ],
            }
        )

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/settings", json={"default_provider_id": "openai"})

        assert response.status_code == 200
        raw_settings = await database.load_settings()
        assert raw_settings["default_provider_id"] == "openai"
        assert raw_settings["api_key"] == "openai-secret"
        assert raw_settings["base_url"] == "https://api.openai.com/v1"
        assert raw_settings["model_id"] == "gpt-4.1"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_provider_normalization_strips_gemini_25_models():
    from backend.app.providers import (
        first_model_id,
        is_unsupported_model_id,
        mask_provider_secrets,
        normalize_providers,
    )

    providers = normalize_providers(
        [
            {
                "id": "mixed",
                "name": "Mixed",
                "api_key": "secret",
                "base_url": "https://api.example.com/v1",
                "model_id": (
                    "gemini-2.5-pro:Gemini 2.5 Pro, "
                    "gemini-1.5-pro:Gemini 1.5 Pro, "
                    "deepseek-chat:DeepSeek Chat"
                ),
            }
        ]
    )

    assert is_unsupported_model_id("Gemini 2.5 Flash Lite")
    assert not is_unsupported_model_id("gemini-1.5-pro")
    assert providers[0]["model_id"] == (
        "gemini-1.5-pro:Gemini 1.5 Pro, deepseek-chat:DeepSeek Chat"
    )

    masked = mask_provider_secrets(
        {
            "model_id": "gemini-2.5-flash:Gemini 2.5 Flash, gpt-4.1",
            "providers": [
                {
                    "id": "mixed",
                    "api_key": "secret",
                    "model_id": "gemini-2.5-flash:Gemini 2.5 Flash, gpt-4.1",
                }
            ],
        }
    )
    assert masked["model_id"] == "gpt-4.1"
    assert masked["providers"][0]["model_id"] == "gpt-4.1"
    assert first_model_id("gpt-4.1:GPT 4.1") == "gpt-4.1"
    assert first_model_id("gpt-5.5::5.5") == "gpt-5.5"
    assert first_model_id("gpt-5.5:5.5") == "gpt-5.5"
    assert first_model_id("deepseek-v4-pro:pro") == "deepseek-v4-pro"
    assert first_model_id("gpt-5.5:gpt-5.5") == "gpt-5.5"
    assert first_model_id("deepseek-chat:DeepSeek 聊天") == "deepseek-chat"
    assert first_model_id("qwen2.5:7b") == "qwen2.5:7b"
    assert first_model_id("qwen2.5:7b::Qwen 7B") == "qwen2.5:7b"
    assert first_model_id("foo::") == "foo::"


def test_loaded_settings_backfills_legacy_api_into_default_provider():
    from backend.app.database import _normalize_loaded_settings
    from backend.app.providers import mask_provider_secrets

    settings = _normalize_loaded_settings(
        {
            "default_provider_id": "deepseek",
            "api_key": "legacy-secret-1234",
            "base_url": "https://legacy.example/v1",
            "model_id": "legacy-model:Legacy Model",
            "providers": [
                {
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "api_key": "",
                    "base_url": "https://api.deepseek.com/v1",
                    "model_id": "deepseek-v4-pro",
                }
            ],
        },
        has_stored_providers=True,
    )

    provider = settings["providers"][0]
    assert provider["api_key"] == "legacy-secret-1234"
    assert provider["base_url"] == "https://legacy.example/v1"
    assert provider["model_id"] == "legacy-model:Legacy Model"

    masked = mask_provider_secrets(settings)
    assert masked["api_key"] == "leg****1234"
    assert masked["providers"][0]["api_key"] == "leg****1234"


def test_loaded_settings_backfill_missing_workflow_step_models():
    from backend.app.database import _normalize_loaded_settings

    settings = _normalize_loaded_settings(
        {
            "default_provider_id": "openai",
            "providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "api_key": "secret",
                    "base_url": "https://api.openai.com/v1",
                    "model_id": "gpt-4.1",
                }
            ],
            "workflow_step_models": {
                "analysis": {"provider_id": "openai", "model_id": "gpt-4.1"}
            },
        }
    )

    assert settings["workflow_step_models"]["analysis"] == {
        "provider_id": "openai",
        "model_id": "gpt-4.1",
    }
    assert settings["workflow_step_models"]["relevance"] == {
        "provider_id": "",
        "model_id": "",
    }
    assert settings["workflow_step_models"]["interaction"] == {
        "provider_id": "",
        "model_id": "",
    }
    assert settings["workflow_step_models"]["answer"] == {
        "provider_id": "",
        "model_id": "",
    }


def test_workflow_step_model_resolution_reuses_fallback_api_key(monkeypatch):
    from backend.app.routers.chat import _resolve_workflow_step_models

    calls = []

    async def fake_next_api_key(api_keys):
        calls.append(api_keys)
        return api_keys.split(",", 1)[0]

    monkeypatch.setattr("backend.app.routers.chat.get_next_api_key", fake_next_api_key)

    settings = {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "api_key": "openai-key-1,openai-key-2",
                "base_url": "https://api.openai.com/v1",
                "model_id": "gpt-4.1",
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "api_key": "deepseek-key-1,deepseek-key-2",
                "base_url": "https://api.deepseek.com/v1",
                "model_id": "deepseek-chat",
            },
        ],
        "workflow_step_models": {
            "analysis": {"provider_id": "", "model_id": ""},
            "relevance": {"provider_id": "deepseek", "model_id": "deepseek-chat"},
            "interaction": {"provider_id": "", "model_id": ""},
            "answer": {"provider_id": "", "model_id": ""},
        },
    }

    resolved = asyncio.run(
        _resolve_workflow_step_models(
            settings,
            "openai",
            "openai-key-1",
            "gpt-4.1",
        )
    )

    assert calls == ["deepseek-key-1,deepseek-key-2"]
    assert resolved["analysis"]["api_key"] == "openai-key-1"
    assert resolved["interaction"]["api_key"] == "openai-key-1"
    assert resolved["answer"]["api_key"] == "openai-key-1"
    assert resolved["relevance"]["api_key"] == "deepseek-key-1"


def test_workflow_step_model_resolution_uses_selected_provider_default_model(monkeypatch):
    from backend.app.routers.chat import _resolve_workflow_step_models

    async def fake_next_api_key(api_keys):
        return api_keys.split(",", 1)[0]

    monkeypatch.setattr("backend.app.routers.chat.get_next_api_key", fake_next_api_key)

    settings = {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "api_key": "openai-key",
                "base_url": "https://api.openai.com/v1",
                "model_id": "gpt-4.1",
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "api_key": "deepseek-key",
                "base_url": "https://api.deepseek.com/v1",
                "model_id": "deepseek-chat, deepseek-reasoner",
            },
        ],
        "workflow_step_models": {
            "analysis": {"provider_id": "deepseek", "model_id": ""},
            "relevance": {"provider_id": "", "model_id": ""},
            "interaction": {"provider_id": "", "model_id": ""},
            "answer": {"provider_id": "", "model_id": ""},
        },
    }

    resolved = asyncio.run(
        _resolve_workflow_step_models(
            settings,
            "openai",
            "openai-key",
            "gpt-4.1",
        )
    )

    assert resolved["analysis"]["provider_id"] == "deepseek"
    assert resolved["analysis"]["model"] == "deepseek-chat"
    assert resolved["relevance"]["provider_id"] == "openai"
    assert resolved["relevance"]["model"] == "gpt-4.1"


def test_workflow_step_model_resolution_allows_empty_local_api_key():
    from backend.app.routers.chat import _resolve_workflow_step_models

    settings = {
        "providers": [
            {
                "id": "ollama",
                "name": "Ollama",
                "api_key": "",
                "base_url": "http://host.docker.internal:11434/v1",
                "model_id": "llama3.1",
            },
        ],
        "workflow_step_models": {
            "analysis": {"provider_id": "ollama", "model_id": "llama3.1"},
            "relevance": {"provider_id": "", "model_id": ""},
            "interaction": {"provider_id": "", "model_id": ""},
            "answer": {"provider_id": "", "model_id": ""},
        },
    }

    resolved = asyncio.run(
        _resolve_workflow_step_models(
            settings,
            "ollama",
            "",
            "llama3.1",
        )
    )

    assert resolved["analysis"]["provider_id"] == "ollama"
    assert resolved["analysis"]["api_key"] == ""
    assert resolved["analysis"]["model"] == "llama3.1"
    assert resolved["answer"]["api_key"] == ""


def test_workflow_step_model_resolution_rejects_remote_provider_without_api_key():
    from fastapi import HTTPException
    from backend.app.routers.chat import _resolve_workflow_step_models

    settings = {
        "providers": [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "api_key": "",
                "base_url": "https://api.deepseek.com/v1",
                "model_id": "deepseek-v4-pro",
            },
        ],
        "workflow_step_models": {
            "analysis": {"provider_id": "deepseek", "model_id": "deepseek-v4-pro"},
        },
    }

    try:
        asyncio.run(
            _resolve_workflow_step_models(
                settings,
                "deepseek",
                "",
                "deepseek-v4-pro",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "请先在设置中配置 API 密钥（DeepSeek）。"
    else:
        raise AssertionError("expected missing remote provider key to be rejected")


def test_workflow_step_model_resolution_rejects_unsupported_step_model():
    from fastapi import HTTPException
    from backend.app.routers.chat import _resolve_workflow_step_models

    settings = {
        "providers": [
            {
                "id": "google",
                "name": "Google",
                "api_key": "google-key",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model_id": "gemini-2.5-pro",
            },
        ],
        "workflow_step_models": {
            "analysis": {"provider_id": "google", "model_id": "gemini-2.5-pro"},
        },
    }

    try:
        asyncio.run(
            _resolve_workflow_step_models(
                settings,
                "google",
                "google-key",
                "gemini-2.5-pro",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Gemini 2.5 系列模型不再支持"
    else:
        raise AssertionError("expected unsupported workflow step model to be rejected")


def test_chat_endpoint_rejects_remote_provider_without_api_key(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    class UnexpectedWorkflow:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("chat endpoint should fail before starting workflow")

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-v4-pro",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", UnexpectedWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "missing-key-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "请先在设置中配置 API 密钥（DeepSeek）。"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_rejects_blank_query_before_settings_lookup(monkeypatch):
    from backend.app.routers.chat import router

    async def unexpected_load_settings():
        raise AssertionError("blank query should fail before settings are loaded")

    class UnexpectedWorkflow:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("blank query should fail before workflow starts")

    async def run():
        monkeypatch.setattr("backend.app.routers.chat.load_settings", unexpected_load_settings)
        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", UnexpectedWorkflow)

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "   \n\t  ",
                    "session_id": "blank-query-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "query 不能为空"

    asyncio.run(run())


def test_chat_endpoint_rejects_route_unsafe_session_id(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    class UnexpectedWorkflow:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("chat endpoint should reject session_id before starting workflow")

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", UnexpectedWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "bad/session",
                    "provider_id": "missing-provider",
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "session_id 格式无效"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_delete_message_endpoint_rejects_route_unsafe_session_id(tmp_path):
    from backend.app import database
    from backend.app.routers.chat import router

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

        async with await database.get_session() as session:
            session.add(
                database.ChatSession(
                    id="bad\\session",
                    title="Dirty Session",
                )
            )
            session.add(
                database.ChatMessage(
                    session_id="bad\\session",
                    role="user",
                    content="do not delete",
                )
            )
            await session.commit()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.request(
                "DELETE",
                "/api/chat/message",
                json={"session_id": "bad\\session", "message_index": 0},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "session_id 格式无效"

        async with await database.get_session() as session:
            remaining = (
                await session.execute(
                    text("SELECT count(*) FROM chat_messages WHERE session_id = 'bad\\session'")
                )
            ).scalar_one()

        assert remaining == 1

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_uses_selected_provider_id(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured.update(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "model": model,
                    "search_engine": search_engine,
                    "max_results": max_results,
                    "max_iterations": max_iterations,
                    "interactive_search": interactive_search,
                    "session_id": session_id,
                    "max_concurrent_pages": max_concurrent_pages,
                    "step_model_configs": step_model_configs,
                    "canvas_mode": canvas_mode,
                    "live_artifacts_mode": live_artifacts_mode,
                }
            )

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            progress_callback("started")
            stats_callback({"provider": captured["model"]})
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "api_key": "openai-secret",
                        "base_url": "https://api.openai.com/v1",
                        "model_id": "gpt-4.1",
                    },
                ],
                "search_engine": "bing",
                "max_results": 7,
                "max_iterations": 3,
                "interactive_search": False,
                "max_concurrent_pages": 2,
                "workflow_step_models": {
                    "analysis": {"provider_id": "deepseek", "model_id": "deepseek-chat"},
                    "relevance": {"provider_id": "openai", "model_id": "gpt-4.1"},
                    "interaction": {"provider_id": "", "model_id": ""},
                    "answer": {"provider_id": "openai", "model_id": "gpt-4.1"},
                },
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "provider-test",
                    "provider_id": "openai",
                    "max_concurrent_pages": 9,
                    "canvas_mode": True,
                },
            )
            body = response.text

        assert response.status_code == 200
        assert "answer for hello" in body
        assert captured["api_key"] == "openai-secret"
        assert captured["base_url"] == "https://api.openai.com/v1"
        assert captured["model"] == "gpt-4.1"
        assert captured["search_engine"] == "bing"
        assert captured["max_results"] == 7
        assert captured["max_iterations"] == 3
        assert captured["interactive_search"] is False
        assert captured["max_concurrent_pages"] == 9
        assert captured["canvas_mode"] is False
        assert captured["live_artifacts_mode"] is True
        assert captured["step_model_configs"]["analysis"]["provider_id"] == "deepseek"
        assert captured["step_model_configs"]["analysis"]["api_key"] == "deepseek-secret"
        assert captured["step_model_configs"]["analysis"]["model"] == "deepseek-chat"
        assert captured["step_model_configs"]["interaction"]["provider_id"] == "openai"
        assert captured["step_model_configs"]["interaction"]["model"] == "gpt-4.1"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_clamps_request_search_limits(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured.update(
                {
                    "max_results": max_results,
                    "max_iterations": max_iterations,
                    "max_concurrent_pages": max_concurrent_pages,
                }
            )

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
                "max_results": 8,
                "max_iterations": 4,
                "max_concurrent_pages": 6,
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "limit-clamp-test",
                    "provider_id": "deepseek",
                    "max_results": 500,
                    "max_iterations": -2,
                    "max_concurrent_pages": 0,
                },
            )

        assert response.status_code == 200
        assert captured == {
            "max_results": 50,
            "max_iterations": 1,
            "max_concurrent_pages": 1,
        }

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_defaults_to_searxng_when_no_engine_is_saved(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured["search_engine"] = search_engine
            captured["live_artifacts_mode"] = live_artifacts_mode

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "default-engine-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 200
        assert captured["search_engine"] == "searxng"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_rejects_unknown_requested_search_engine(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    class UnexpectedWorkflow:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("invalid search engine should fail before workflow starts")

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", UnexpectedWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "bad-engine-test",
                    "provider_id": "deepseek",
                    "search_engine": "not-a-real-engine",
                },
            )

        assert response.status_code == 400
        assert "不支持的搜索引擎" in response.json()["detail"]

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_falls_back_when_saved_search_engine_is_unknown(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured["search_engine"] = search_engine

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "search_engine": "retired-engine",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "saved-bad-engine-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 200
        assert captured["search_engine"] == "searxng"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_falls_back_when_saved_search_engine_is_not_string(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured["search_engine"] = search_engine

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "search_engine": 123,
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "numeric-engine-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 200
        assert captured["search_engine"] == "searxng"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_saves_latest_source_snapshot_without_duplicates(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    class FakeWorkflow:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            source_callback([
                {"id": 1, "title": "A", "url": "https://a.example"},
                {"id": 2, "title": "B", "url": "https://b.example"},
            ])
            source_callback([
                {"id": 1, "title": "A", "url": "https://a.example"},
                {"id": 2, "title": "B", "url": "https://b.example"},
                {"id": 3, "title": "C", "url": "https://c.example"},
            ])
            stats_callback({"sites_searched": 3})
            return f"answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "deepseek",
                "providers": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "api_key": "deepseek-secret",
                        "base_url": "https://api.deepseek.com/v1",
                        "model_id": "deepseek-chat",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "session_id": "source-snapshot-test",
                    "provider_id": "deepseek",
                },
            )

        assert response.status_code == 200
        history = await database.load_chat_history("source-snapshot-test")
        assistant = history["messages"][1]
        assert [source["id"] for source in assistant["sources"]] == [1, 2, 3]

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_allows_local_provider_without_api_key(tmp_path, monkeypatch):
    from backend.app import database
    from backend.app.routers.chat import router

    captured = {}

    class FakeWorkflow:
        def __init__(
            self,
            api_key,
            base_url,
            model,
            search_engine,
            max_results,
            max_iterations,
            interactive_search,
            session_id=None,
            max_concurrent_pages=3,
            step_model_configs=None,
            canvas_mode=False,
            live_artifacts_mode=False,
        ):
            captured.update(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "model": model,
                    "step_model_configs": step_model_configs,
                    "live_artifacts_mode": live_artifacts_mode,
                }
            )

        async def run(
            self,
            query,
            progress_callback,
            stream_callback,
            context_messages,
            source_callback,
            stats_callback,
        ):
            progress_callback("started")
            return f"local answer for {query}"

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
        await database.save_settings(
            {
                "default_provider_id": "ollama",
                "providers": [
                    {
                        "id": "ollama",
                        "name": "Ollama",
                        "api_key": "",
                        "base_url": "http://host.docker.internal:11434/v1",
                        "model_id": "llama3.1",
                    },
                ],
                "workflow_step_models": {
                    "analysis": {"provider_id": "ollama", "model_id": "llama3.1"},
                    "relevance": {"provider_id": "", "model_id": ""},
                    "interaction": {"provider_id": "", "model_id": ""},
                    "answer": {"provider_id": "", "model_id": ""},
                },
            }
        )

        from backend.app.rate_limiter import chat_limiter

        monkeypatch.setattr("backend.app.routers.chat.SearchWorkflow", FakeWorkflow)
        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello local",
                    "session_id": "local-provider-test",
                    "provider_id": "ollama",
                },
            )
            body = response.text

        assert response.status_code == 200
        assert "local answer for hello local" in body
        assert captured["api_key"] == ""
        assert captured["base_url"] == "http://host.docker.internal:11434/v1"
        assert captured["model"] == "llama3.1"
        assert captured["step_model_configs"]["analysis"]["api_key"] == ""

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_chat_endpoint_rejects_gemini_25_model_request(tmp_path):
    from backend.app import database
    from backend.app.routers.chat import router

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
        await database.save_settings(
            {
                "default_provider_id": "gemini",
                "providers": [
                    {
                        "id": "gemini",
                        "name": "Gemini",
                        "api_key": "token",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                        "model_id": "gemini-1.5-pro",
                    },
                ],
            }
        )

        from backend.app.rate_limiter import chat_limiter

        chat_limiter._requests.clear()

        app = FastAPI()
        app.include_router(router)

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat",
                json={
                    "query": "hello",
                    "provider_id": "gemini",
                    "model": "gemini-2.5-pro",
                },
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Gemini 2.5 系列模型不再支持"

        if database._engine is not None:
            await database._engine.dispose()
            database._engine = None
            database._async_session_factory = None

    asyncio.run(run())


def test_check_search_engines_reports_availability(monkeypatch):
    from backend.app.routers.settings import router
    from backend.app.engine_health import engine_health

    calls = []
    engine_health._results.clear()

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
            health_batch_id=None,
        ):
            calls.append(
                {
                    "engine": self.engine,
                    "max_results": self.max_results,
                    "query": query,
                    "allow_fallback": allow_fallback,
                    "use_cache": use_cache,
                    "health_batch_id": health_batch_id,
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
                    "reason": "",
                    "error": "",
                },
                {
                    "engine": "bing",
                    "available": False,
                    "result_count": 0,
                    "reason": "",
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
                "health_batch_id": None,
            },
            {
                "engine": "bing",
                "max_results": 3,
                "query": "JustSearch test",
                "allow_fallback": False,
                "use_cache": False,
                "health_batch_id": None,
            },
        ]

    asyncio.run(run())


def test_check_search_engines_reports_recent_failure_reason(monkeypatch):
    from backend.app.routers.settings import router
    from backend.app.engine_health import engine_health

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
            health_batch_id=None,
        ):
            engine_health.record(self.engine, success=False, reason="blocked")
            return []

    engine_health._results.clear()
    monkeypatch.setattr(
        "backend.app.routers.settings.BrowserManager",
        FakeBrowserManager,
    )
    monkeypatch.setattr(
        "backend.app.routers.settings.get_all_engines",
        lambda: ["brave"],
    )

    app = FastAPI()
    app.include_router(router)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/settings/check-engines")

        assert response.status_code == 200
        assert response.json()["results"] == [
            {
                "engine": "brave",
                "available": False,
                "result_count": 0,
                "reason": "blocked",
                "error": "验证/反爬页面",
            }
        ]

    asyncio.run(run())


def test_engines_endpoint_returns_full_configured_engine_list(monkeypatch):
    from backend.app import search_engine
    from backend.app.routers.stats import router

    monkeypatch.setattr(search_engine, "_config_cache", {})
    monkeypatch.setattr(search_engine, "_config_mtime", 0.0)

    app = FastAPI()
    app.include_router(router)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/engines")

        assert response.status_code == 200
        assert response.json() == {
            "engines": [
                "google",
                "bing",
                "duckduckgo",
                "sogou",
                "brave",
                "searxng",
            ]
        }

    asyncio.run(run())
