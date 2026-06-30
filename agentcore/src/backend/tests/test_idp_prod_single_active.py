"""Unit test for PROD single-active: when a PROD version becomes active, every OTHER active PROD
version for the agent is deactivated (mirrors the existing UAT publish behaviour). Pure/fast — uses
a fake session, no DB.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentcore.api.publish import _deactivate_other_active_prod_versions


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal SQLModel-style async session: exec() returns the preset rows; add() records them."""
    def __init__(self, rows):
        self._rows = rows
        self.added = []

    async def exec(self, *_a, **_k):
        return _FakeExecResult(self._rows)

    def add(self, obj):
        self.added.append(obj)


def _prod_row(active=True):
    return SimpleNamespace(id=uuid4(), is_active=active)


@pytest.mark.anyio
async def test_deactivates_all_other_active_prod_versions():
    rows = [_prod_row(active=True), _prod_row(active=True), _prod_row(active=True)]
    session = _FakeSession(rows)
    n = await _deactivate_other_active_prod_versions(session, uuid4(), uuid4())
    assert n == 3
    assert all(r.is_active is False for r in rows)        # every other active version turned off
    assert {id(o) for o in session.added} == {id(r) for r in rows}  # each persisted


@pytest.mark.anyio
async def test_no_other_active_versions_is_a_noop():
    session = _FakeSession([])
    n = await _deactivate_other_active_prod_versions(session, uuid4(), uuid4())
    assert n == 0
    assert session.added == []
