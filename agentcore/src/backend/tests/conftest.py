"""Shared pytest configuration for the backend test suite.

Windows event-loop fix: psycopg's async driver cannot run on the ``ProactorEventLoop`` that
Windows uses by default, so any test that opens a real async DB session (the IDP pipeline hooks
each open their own ``session_scope()``) fails with an ``InterfaceError`` on Windows. Selecting the
``WindowsSelectorEventLoopPolicy`` — the same loop the app runs under via ``uvicorn --reload`` —
makes those tests behave as they do on macOS/Linux. Guarded to Windows so other platforms are
unaffected.
"""

import sys

import pytest

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _default_idp_engine_fixed(monkeypatch):
    """Tests must be insensitive to a developer's ``IDP_EXECUTION_ENGINE=graph`` in ``.env``: default
    every test to the fixed pipeline. Graph-engine tests opt in explicitly with
    ``monkeypatch.setenv("IDP_EXECUTION_ENGINE", "graph")`` (which overrides this), and the parity
    tests ``delenv`` it for their legacy leg. Without this, running the suite while the app is
    configured for the graph engine would push legacy tests through the wrong engine."""
    monkeypatch.setenv("IDP_EXECUTION_ENGINE", "fixed")
