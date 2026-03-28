"""
Legacy shim – all functions are now backed by the SQLite database.
This module re-exports everything from database.py for backward compatibility.
"""

from .database import (  # noqa: F401
    list_chats,
    load_chat_history,
    save_chat_history,
    delete_chat,
    get_chat_path,
    delete_all_chats,
)
