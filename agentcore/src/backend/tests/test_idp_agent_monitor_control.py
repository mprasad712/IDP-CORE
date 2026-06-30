"""Unit tests for controlling an agent's background monitors/schedules from the Agent Control Panel.

Covers two helpers added to api/triggers.py:
  - _normalize_schedule_config: fixes the frontend(cron) <-> backend(cron_expression/schedule_type)
    mismatch so a created schedule honours the entered cron instead of silently running hourly.
  - set_deployment_triggers_active: activates(register) / deactivates(unregister) ALL of a
    deployment's triggers so monitors follow the agent's live state (Start/Stop/Enable/Disable).
Pure/fast: no DB, no Graph, no scheduler — _register_trigger/_unregister_trigger are monkeypatched.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentcore.api import triggers as trig
from agentcore.api.triggers import (
    _normalize_schedule_config,
    deactivate_other_deployment_monitors,
    set_deployment_triggers_active,
)
from agentcore.services.database.models.trigger_config.model import TriggerTypeEnum


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── _normalize_schedule_config (Fix C: cron honoured) ────────────────

def test_normalize_promotes_cron_to_cron_expression():
    out = _normalize_schedule_config({"cron": "0 9 * * *"})
    assert out["cron_expression"] == "0 9 * * *"
    assert out["schedule_type"] == "cron"
    assert "cron" not in out  # stale alias dropped


def test_normalize_keeps_existing_cron_expression():
    out = _normalize_schedule_config({"cron_expression": "*/5 * * * *"})
    assert out["cron_expression"] == "*/5 * * * *"
    assert out["schedule_type"] == "cron"


def test_normalize_cron_expression_wins_over_alias():
    out = _normalize_schedule_config({"cron_expression": "A A A A A", "cron": "B B B B B"})
    assert out["cron_expression"] == "A A A A A"
    assert "cron" not in out


def test_normalize_leaves_interval_schedule_untouched():
    out = _normalize_schedule_config({"schedule_type": "interval", "interval_minutes": 30})
    assert out == {"schedule_type": "interval", "interval_minutes": 30}
    assert "cron_expression" not in out


def test_normalize_empty_is_inert():
    assert _normalize_schedule_config({}) == {}
    assert _normalize_schedule_config(None) == {}


# ── set_deployment_triggers_active (Fix A: Start/Stop → monitors) ────

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """Minimal async session: execute() returns the preset rows; add() records the row."""
    def __init__(self, rows):
        self._rows = rows
        self.added = []

    async def execute(self, *_a, **_k):
        return _FakeResult(self._rows)

    def add(self, obj):
        self.added.append(obj)


def _row(active=True, ttype=TriggerTypeEnum.EMAIL_MONITOR):
    return SimpleNamespace(id=uuid4(), trigger_type=ttype, is_active=active, deployment_id=uuid4())


@pytest.fixture
def capture_register(monkeypatch):
    reg, unreg = [], []

    async def _fake_register(record):
        reg.append(record)

    async def _fake_unregister(trigger_id, trigger_type):
        unreg.append((trigger_id, trigger_type))

    monkeypatch.setattr(trig, "_register_trigger", _fake_register)
    monkeypatch.setattr(trig, "_unregister_trigger", _fake_unregister)
    return reg, unreg


@pytest.mark.anyio
async def test_deactivate_all_triggers_for_deployment(capture_register):
    reg, unreg = capture_register
    rows = [_row(active=True), _row(active=True, ttype=TriggerTypeEnum.SCHEDULE)]
    session = _FakeSession(rows)
    n = await set_deployment_triggers_active(session, uuid4(), active=False)
    assert n == 2
    assert all(r.is_active is False for r in rows)            # persisted intent
    assert {u[0] for u in unreg} == {r.id for r in rows}       # every type unregistered
    assert reg == []                                           # nothing registered


@pytest.mark.anyio
async def test_activate_all_triggers_for_deployment(capture_register):
    reg, unreg = capture_register
    rows = [_row(active=False), _row(active=True)]  # mix: coarse "turn all on"
    session = _FakeSession(rows)
    n = await set_deployment_triggers_active(session, uuid4(), active=True)
    assert n == 2
    assert all(r.is_active is True for r in rows)
    assert {id(r) for r in reg} == {id(r) for r in rows}       # every row registered
    assert unreg == []


@pytest.mark.anyio
async def test_deactivate_other_deployment_monitors_stops_siblings(capture_register):
    # Single live monitor per agent+env: when one version becomes live, the OTHER deployments'
    # monitors (siblings) must be stopped — not left polling.
    reg, unreg = capture_register
    siblings = [_row(active=True), _row(active=True, ttype=TriggerTypeEnum.SCHEDULE)]
    session = _FakeSession(siblings)
    n = await deactivate_other_deployment_monitors(session, uuid4(), "uat", uuid4())
    assert n == 2
    assert all(r.is_active is False for r in siblings)         # siblings turned off
    assert {u[0] for u in unreg} == {r.id for r in siblings}   # each sibling monitor unregistered
    assert reg == []                                           # nothing re-registered


@pytest.mark.anyio
async def test_one_trigger_failure_does_not_abort_the_rest(monkeypatch):
    rows = [_row(active=True), _row(active=True)]
    session = _FakeSession(rows)
    calls = []

    async def _flaky_unregister(trigger_id, trigger_type):
        calls.append(trigger_id)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(trig, "_unregister_trigger", _flaky_unregister)
    # Should not raise; the second row is still processed.
    n = await set_deployment_triggers_active(session, uuid4(), active=False)
    assert len(calls) == 2
    assert n == 1  # only the successful one is counted
