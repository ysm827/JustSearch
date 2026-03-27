import asyncio
import time
import logging

logger = logging.getLogger(__name__)

# Store pages that need user interaction: session_id -> { "page": page, "event": asyncio.Event() }
_INTERACTION_SESSIONS = {}


def get_interaction_session(session_id: str):
    return _INTERACTION_SESSIONS.get(session_id)


async def mark_interaction_completed(session_id: str):
    if session_id in _INTERACTION_SESSIONS:
        _INTERACTION_SESSIONS[session_id]["event"].set()


def register_interaction_session(session_id: str, page, event: asyncio.Event):
    """Register a new interaction session for CAPTCHA solving etc."""
    _INTERACTION_SESSIONS[session_id] = {
        "page": page,
        "event": event,
        "last_active": time.time()
    }


def remove_interaction_session(session_id: str):
    """Remove an interaction session."""
    _INTERACTION_SESSIONS.pop(session_id, None)
