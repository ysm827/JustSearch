"""Shared pytest fixtures for JustSearch tests."""

import pytest


@pytest.fixture(autouse=True)
def mock_extension_bridge_connected(monkeypatch):
    """Chat workflows require the Chrome bridge; default tests to connected.

    Individual tests can override with:
        monkeypatch.setattr(
            "backend.app.routers.chat.is_extension_connected",
            lambda: False,
        )
    """
    monkeypatch.setattr(
        "backend.app.extension_bridge.is_extension_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "backend.app.routers.chat.is_extension_connected",
        lambda: True,
    )
