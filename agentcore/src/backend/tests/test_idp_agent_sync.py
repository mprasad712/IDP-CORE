"""Tests for auto-creating/syncing the IdpAgent row when an agent with IDP nodes is saved."""

from uuid import uuid4

import pytest
from sqlmodel import select

from agentcore.services.idp.agent_config import sync_idp_agent_from_graph, agent_contains_idp_nodes


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _node(display_name: str, fields: dict) -> dict:
    return {
        "id": display_name.replace(" ", ""),
        "data": {"node": {"display_name": display_name, "template": {k: {"value": v} for k, v in fields.items()}}},
    }


def _idp_graph(mode: str = "field_configuration") -> dict:
    return {
        "nodes": [_node("AI Field Extractor", {"extraction_mode": mode}), _node("Processed Docs Output", {})],
        "edges": [],
    }


def test_agent_contains_idp_nodes():
    assert agent_contains_idp_nodes(_idp_graph()) is True
    assert agent_contains_idp_nodes({"nodes": [_node("Chat Input", {})], "edges": []}) is False
    assert agent_contains_idp_nodes(None) is False


async def _make_agent(session, data):
    from agentcore.services.database.models.agent.model import Agent

    a = Agent(id=uuid4(), name=f"sync-test-{uuid4().hex[:8]}", data=data)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _cleanup(agent_id):
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    async with session_scope() as session:
        for ia in (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).all():
            await session.delete(ia)
        a = await session.get(Agent, agent_id)
        if a:
            await session.delete(a)
        await session.commit()


@pytest.mark.anyio
async def test_sync_creates_idp_agent_for_idp_graph():
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    async with session_scope() as session:
        agent = await _make_agent(session, _idp_graph("field_configuration"))
    try:
        async with session_scope() as session:
            db_agent = await session.get(Agent, agent.id)
            await sync_idp_agent_from_graph(session, db_agent, None)
        async with session_scope() as session:
            ia = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent.id))).first()
            assert ia is not None
            assert ia.extraction_mode == "named_config"  # field_configuration -> named_config
            assert ia.is_active is True
    finally:
        await _cleanup(agent.id)


@pytest.mark.anyio
async def test_sync_noop_for_non_idp_graph():
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    async with session_scope() as session:
        agent = await _make_agent(session, {"nodes": [_node("Chat Input", {})], "edges": []})
    try:
        async with session_scope() as session:
            db_agent = await session.get(Agent, agent.id)
            await sync_idp_agent_from_graph(session, db_agent, None)
        async with session_scope() as session:
            ia = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent.id))).first()
            assert ia is None  # non-IDP agent -> no IdpAgent created
    finally:
        await _cleanup(agent.id)


@pytest.mark.anyio
async def test_sync_reactivates_soft_deleted_idp_agent():
    """A soft-deleted IdpAgent must be reactivated (not duplicate-inserted) when the agent is re-saved.

    agent_id is UNIQUE across all rows incl. soft-deleted, so a blind INSERT would raise."""
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    async with session_scope() as session:
        agent = await _make_agent(session, _idp_graph("dynamic_prompt"))
        # pre-existing SOFT-DELETED IdpAgent for this agent
        session.add(IdpAgent(agent_id=agent.id, extraction_mode="dynamic_prompting",
                             default_rule_action="pending_review", is_active=False,
                             deleted_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        await session.commit()
    try:
        async with session_scope() as session:
            await sync_idp_agent_from_graph(session, await session.get(Agent, agent.id), None)
        async with session_scope() as session:
            rows = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent.id))).all()
            assert len(rows) == 1  # reactivated, not duplicated
            assert rows[0].deleted_at is None and rows[0].is_active is True
    finally:
        await _cleanup(agent.id)


@pytest.mark.anyio
async def test_sync_idempotent_and_updates_mode():
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent
    from agentcore.services.deps import session_scope

    async with session_scope() as session:
        agent = await _make_agent(session, _idp_graph("dynamic_prompt"))
    try:
        # first sync -> dynamic_prompting
        async with session_scope() as session:
            await sync_idp_agent_from_graph(session, await session.get(Agent, agent.id), None)
        async with session_scope() as session:
            ia = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent.id))).first()
            assert ia.extraction_mode == "dynamic_prompting"

        # change the graph mode + re-sync -> still ONE row, mode updated (idempotent upsert)
        async with session_scope() as session:
            a = await session.get(Agent, agent.id)
            a.data = _idp_graph("field_configuration")
            session.add(a)
            await session.commit()
        async with session_scope() as session:
            await sync_idp_agent_from_graph(session, await session.get(Agent, agent.id), None)
        async with session_scope() as session:
            rows = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent.id))).all()
            assert len(rows) == 1  # no duplicate
            assert rows[0].extraction_mode == "named_config"  # mode updated
    finally:
        await _cleanup(agent.id)
