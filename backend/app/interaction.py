import asyncio
import time
import logging

logger = logging.getLogger(__name__)

# 存储需要用户交互的页面: session_id -> { "page": page, "event": asyncio.Event() }
_INTERACTION_SESSIONS = {}
_SESSION_TTL = 1800  # 会话过期时间：30 分钟


def get_interaction_session(session_id: str):
    return _INTERACTION_SESSIONS.get(session_id)


async def mark_interaction_completed(session_id: str):
    if session_id in _INTERACTION_SESSIONS:
        _INTERACTION_SESSIONS[session_id]["event"].set()


def register_interaction_session(session_id: str, page, event: asyncio.Event):
    """注册新的交互会话（用于验证码解决等场景），并清理过期会话。"""
    # 清理超过 30 分钟未活跃的会话
    now = time.time()
    expired = [sid for sid, sess in _INTERACTION_SESSIONS.items()
               if now - sess.get("last_active", 0) > _SESSION_TTL]
    for sid in expired:
        logger.info("清理过期交互会话: %s", sid)
        _INTERACTION_SESSIONS.pop(sid, None)

    _INTERACTION_SESSIONS[session_id] = {
        "page": page,
        "event": event,
        "last_active": time.time()
    }


def remove_interaction_session(session_id: str):
    """Remove an interaction session."""
    _INTERACTION_SESSIONS.pop(session_id, None)
