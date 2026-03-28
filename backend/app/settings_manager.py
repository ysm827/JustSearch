"""
Legacy shim – all functions are now backed by the SQLite database.
This module re-exports everything from database.py for backward compatibility.
"""

from .database import (  # noqa: F401
    load_settings,
    save_settings,
    DEFAULT_SETTINGS,
    get_next_api_key,
    mask_api_key,
)

# NOTE: SETTINGS_FILE was removed – settings now live in SQLite.
