"""
SQLite + SQLAlchemy async database module for JustSearch.
Replaces JSON file-based chat and settings storage.
"""

import json
import logging
import os
import re
import shutil
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, JSON, select, delete, update, text, Index
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_DB_PATH = os.path.join(_DATA_DIR, "justsearch.db")
_DATABASE_URL = f"sqlite+aiosqlite:///{_DB_PATH}"

# Legacy paths (for migration)
_CHATS_DIR = os.path.join(_PROJECT_ROOT, "chats")
_SETTINGS_FILE = os.path.join(_PROJECT_ROOT, "settings.json")

# ---------------------------------------------------------------------------
# SQLAlchemy base & engine
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


_engine = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


async def get_session() -> AsyncSession:
    """Return a new async database session."""
    if _async_session_factory is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _async_session_factory()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True)
    title = Column(String, default="新对话")
    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), index=True)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan",
                            order_by="ChatMessage.created_at")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, default="")
    logs = Column(JSON, default=list)
    sources = Column(JSON, default=list)
    stats = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())

    session = relationship("ChatSession", back_populates="messages")


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, default="")


# ---------------------------------------------------------------------------
# Initialisation & migration
# ---------------------------------------------------------------------------


async def init_db():
    """Create the data directory, engine, tables and run legacy migration."""
    global _engine, _async_session_factory

    os.makedirs(_DATA_DIR, exist_ok=True)

    _engine = create_async_engine(
        _DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,  # Recycle connections after 30 minutes
        connect_args={"check_same_thread": False},
    )
    _async_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Enable WAL mode for better concurrent read/write performance
    async with _engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA cache_size=-64000"))  # 64MB cache
        await conn.run_sync(Base.metadata.create_all)

    # Ensure missing columns are added (create_all only creates new tables)
    async with _engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info('chat_messages')"))
        existing_cols = {row[1] for row in result}
        if "stats" not in existing_cols:
            await conn.execute(text(
                "ALTER TABLE chat_messages ADD COLUMN stats JSON DEFAULT '{}'"
            ))
            logger.info("Added missing 'stats' column to chat_messages")

        # Ensure performance indexes exist
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_session_created "
            "ON chat_messages (session_id, created_at)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chat_sessions_updated "
            "ON chat_sessions (updated_at)"
        ))

    logger.info("Database initialised at %s", _DB_PATH)

    # One-time migration from legacy JSON files
    await _migrate_legacy_data()

    # Auto-cleanup: remove sessions older than 90 days with no messages
    await _cleanup_old_sessions()


async def _cleanup_old_sessions(max_age_days: int = 90):
    """Remove sessions older than max_age_days that have been inactive."""
    try:
        cutoff = datetime.now() - __import__('datetime').timedelta(days=max_age_days)
        async with await get_session() as session:
            # Single batch delete — cascade handles messages automatically
            result = await session.execute(
                delete(ChatSession).where(ChatSession.updated_at < cutoff)
            )
            if result.rowcount > 0:
                await session.commit()
                logger.info("Cleaned up %d sessions older than %d days", result.rowcount, max_age_days)
    except Exception as e:
        logger.warning("Session cleanup failed: %s", e)


async def _migrate_legacy_data():
    """If chats/ or settings.json exist, import them into SQLite and remove."""
    await _migrate_chats_dir()
    await _migrate_settings_file()


async def _migrate_chats_dir():
    if not os.path.isdir(_CHATS_DIR):
        return

    import glob
    json_files = glob.glob(os.path.join(_CHATS_DIR, "*.json"))
    if not json_files:
        return

    logger.info("Migrating %d chat JSON files from %s …", len(json_files), _CHATS_DIR)

    async with await get_session() as session:
        for fpath in json_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                session_id = data.get("id") or os.path.splitext(os.path.basename(fpath))[0]
                title = data.get("title", "新对话")
                timestamp_str = data.get("timestamp")
                ts = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()

                # Upsert session
                existing = (await session.execute(
                    select(ChatSession).where(ChatSession.id == session_id)
                )).scalar_one_or_none()
                if existing is None:
                    session_obj = ChatSession(id=session_id, title=title, created_at=ts, updated_at=ts)
                    session.add(session_obj)
                else:
                    session_obj = existing

                # Clear old messages
                await session.execute(
                    delete(ChatMessage).where(ChatMessage.session_id == session_id)
                )

                for msg in data.get("messages", []):
                    session.add(ChatMessage(
                        session_id=session_id,
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                        logs=msg.get("logs") if isinstance(msg.get("logs"), list) else [],
                        sources=msg.get("sources") if isinstance(msg.get("sources"), list) else [],
                        stats=msg.get("stats") if isinstance(msg.get("stats"), dict) else {},
                    ))

            except Exception as e:
                logger.error("Failed to migrate %s: %s", fpath, e)

        await session.commit()

    # Remove legacy directory
    try:
        shutil.rmtree(_CHATS_DIR, ignore_errors=True)
        logger.info("Removed legacy chats/ directory")
    except Exception as e:
        logger.warning("Could not remove chats/ directory: %s", e)


async def _migrate_settings_file():
    if not os.path.isfile(_SETTINGS_FILE):
        return

    logger.info("Migrating settings.json …")
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        async with await get_session() as session:
            for key, value in data.items():
                # Convert non-string values to JSON strings
                if isinstance(value, (dict, list, bool)):
                    str_value = json.dumps(value, ensure_ascii=False)
                else:
                    str_value = str(value) if value is not None else ""

                existing = (await session.execute(
                    select(Settings).where(Settings.key == key)
                )).scalar_one_or_none()

                if existing:
                    existing.value = str_value
                else:
                    session.add(Settings(key=key, value=str_value))

            await session.commit()

        os.remove(_SETTINGS_FILE)
        logger.info("Migrated settings.json and removed file")
    except Exception as e:
        logger.error("Failed to migrate settings.json: %s", e)


# ---------------------------------------------------------------------------
# Chat helpers (drop-in replacements for chat_manager.py)
# ---------------------------------------------------------------------------


async def load_chat_history(session_id_or_path: str) -> Optional[Dict[str, Any]]:
    """
    Load chat history by session_id (or legacy file path for compatibility).
    Returns dict with keys: id, title, timestamp, messages  – same shape as old JSON.
    """
    # If a path is given, extract session_id from filename
    session_id = session_id_or_path
    if os.sep in session_id or "/" in session_id:
        session_id = os.path.splitext(os.path.basename(session_id))[0]

    async with await get_session() as session:
        sess = (await session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )).scalar_one_or_none()

        if sess is None:
            return None

        msgs = (await session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )).scalars().all()

        messages = []
        for m in msgs:
            msg_dict: Dict[str, Any] = {"role": m.role, "content": m.content}
            if m.logs:
                msg_dict["logs"] = m.logs
            if m.sources:
                msg_dict["sources"] = m.sources
            if m.stats:
                msg_dict["stats"] = m.stats
            if m.created_at:
                msg_dict["timestamp"] = m.created_at.isoformat() if hasattr(m.created_at, 'isoformat') else str(m.created_at)
            messages.append(msg_dict)

        return {
            "id": sess.id,
            "title": sess.title,
            "timestamp": sess.updated_at.isoformat() if sess.updated_at else "",
            "messages": messages,
        }


async def save_chat_history(session_id: str, messages: list, title: Optional[str] = None):
    """
    Incrementally append new messages to an existing session.
    Detects which messages are new by comparing against what's already stored.
    Falls back to full upsert for new sessions.
    """
    if not messages:
        return

    async with await get_session() as session:
        # Upsert session row
        sess = (await session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )).scalar_one_or_none()

        now = datetime.now()

        if sess is None:
            if not title:
                first_content = messages[0].get("content", "") if messages else ""
                title = _extract_title(first_content)
            sess = ChatSession(id=session_id, title=title, created_at=now, updated_at=now)
            session.add(sess)
            # New session – insert all messages
            for msg in messages:
                session.add(ChatMessage(
                    session_id=session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    logs=msg.get("logs") if isinstance(msg.get("logs"), list) else [],
                    sources=msg.get("sources") if isinstance(msg.get("sources"), list) else [],
                    stats=msg.get("stats") if isinstance(msg.get("stats"), dict) else {},
                ))
            await session.commit()
            return

        if title:
            sess.title = title
        sess.updated_at = now

        # Count existing messages to determine the append boundary
        existing_count = (await session.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == session_id)
        )).scalar_one()

        new_msgs = messages[existing_count:]
        if new_msgs:
            for msg in new_msgs:
                session.add(ChatMessage(
                    session_id=session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    logs=msg.get("logs") if isinstance(msg.get("logs"), list) else [],
                    sources=msg.get("sources") if isinstance(msg.get("sources"), list) else [],
                    stats=msg.get("stats") if isinstance(msg.get("stats"), dict) else {},
                ))

        await session.commit()


async def list_chats(limit: int = 100, offset: int = 0) -> List[Dict[str, str]]:
    """Return list of chat summaries (id, title, timestamp), newest first. Supports pagination."""
    async with await get_session() as session:
        result = await session.execute(
            select(ChatSession)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        sessions = result.scalars().all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "timestamp": s.updated_at.isoformat() if s.updated_at else "",
            }
            for s in sessions
        ]


async def delete_chat(session_id: str):
    async with await get_session() as session:
        await session.execute(
            delete(ChatSession).where(ChatSession.id == session_id)
        )
        await session.commit()


async def delete_message(session_id: str, message_index: int):
    """Delete a single message by its index (0-based) within a session."""
    async with await get_session() as session:
        msgs = (await session.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )).scalars().all()

        if message_index < 0 or message_index >= len(msgs):
            return False

        await session.delete(msgs[message_index])
        await session.commit()
        return True


async def delete_all_chats():
    async with await get_session() as session:
        await session.execute(delete(ChatMessage))
        await session.execute(delete(ChatSession))
        await session.commit()


def get_chat_path(session_id: str) -> str:
    """
    Legacy compatibility: no longer returns a real file path.
    We return a sentinel that load_chat_history can parse.
    """
    return os.path.join(_CHATS_DIR, f"{session_id}.json")


# ---------------------------------------------------------------------------
# Settings helpers (drop-in replacements for settings_manager.py)
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "theme": "light",
    "api_key": "",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "model_id": "z-ai/glm5,nvidia/nemotron-3-nano-30b-a3b,qwen/qwen3.5-397b-a17b",
    "search_engine": "duckduckgo",
    "max_results": "8",
    "max_iterations": "5",
    "interactive_search": "true",
    "max_concurrent_pages": "10",
    "max_context_turns": "6",
}

_api_key_index = 0
_api_key_lock = asyncio.Lock()


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return api_key[:3] + "****" + api_key[-4:]


async def get_next_api_key(api_keys_str: str) -> str:
    global _api_key_index
    if not api_keys_str:
        return api_keys_str
    keys = [k.strip() for k in api_keys_str.split(",") if k.strip()]
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]
    async with _api_key_lock:
        current_key = keys[_api_key_index % len(keys)]
        _api_key_index = (_api_key_index + 1) % len(keys)
    return current_key


def _extract_title(content: str) -> str:
    """Extract a smart title from the user's first message."""
    if not content:
        return "新对话"
    # Strip common question prefixes
    cleaned = re.sub(r'^(请问|你好|请问一下|我想问|帮我|请|能不能|可以|how\s+to|what\s+is|who\s+is|where\s+is|why\s+is|can\s+you|please\s+)\s*', '', content.strip(), flags=re.IGNORECASE)
    # Take the first sentence (up to sentence-ending punctuation)
    m = re.search(r'^(.+?[。？！\.!?])', cleaned)
    if m:
        sentence = m.group(1).strip()
    else:
        # Take up to first newline or comma separator
        sentence = re.split(r'[\n,，;；]', cleaned)[0].strip()
    # Limit to 50 chars
    if len(sentence) > 50:
        sentence = sentence[:50] + "…"
    return sentence or "新对话"


def _parse_setting_value(raw: str):
    """Try to parse a stored string value back to its original type."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


async def load_settings() -> dict:
    async with await get_session() as session:
        result = await session.execute(select(Settings))
        rows = result.scalars().all()

    settings = DEFAULT_SETTINGS.copy()
    for row in rows:
        settings[row.key] = _parse_setting_value(row.value)
    return settings


async def save_settings(settings: dict) -> bool:
    try:
        async with await get_session() as session:
            for key, value in settings.items():
                if isinstance(value, bool):
                    str_value = json.dumps(value)
                elif isinstance(value, (dict, list)):
                    str_value = json.dumps(value, ensure_ascii=False)
                else:
                    str_value = str(value) if value is not None else ""

                # Use SQLite INSERT OR REPLACE for efficient upsert
                await session.execute(
                    text("INSERT OR REPLACE INTO settings (key, value) VALUES (:k, :v)"),
                    {"k": key, "v": str_value},
                )
            await session.commit()
        return True
    except Exception as e:
        logger.error("Error saving settings: %s", e)
        return False
