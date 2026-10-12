"""Keep the default test suite isolated from credentials and real network calls."""

import os
import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Clear provider configuration and reject real socket connections."""
    prefixes = ("ANTHROPIC_", "OPENAI_", "TAVILY_", "LANGSMITH_", "LANGCHAIN_")
    names = {"GOOGLE_API_KEY", "GEMINI_API_KEY", "DEEP_EQUITY_MODEL", "SEC_USER_AGENT"}
    for name in tuple(os.environ):
        if name.startswith(prefixes) or name in names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    def reject_connection(*args, **kwargs):
        raise AssertionError("Tests must use fake clients or httpx.MockTransport.")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)
    monkeypatch.setattr(socket, "create_connection", reject_connection)
