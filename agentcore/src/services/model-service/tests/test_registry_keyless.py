"""Model-service tests for keyless self-hosted (local LLM / SLM) models.

  - Task 1: openai_compatible is no longer in API_KEY_REQUIRED_PROVIDERS (keyless allowed;
    frontier providers still require a key).
  - Task 2 (BLOCKER): _resolve_registry_config must NOT raise for a keyless openai_compatible
    registry model — the provider applies its own `api_key or "not-needed"` fallback. Frontier
    providers with no stored key still raise.
  - Task 5: the nested provider_config["self_hosted"] marker round-trips create -> read
    (no migration; the JSON blob survives storage and the sanitizer keeps it).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from fastapi import HTTPException

from app.models.registry import (
    ModelRegistry,
    ModelRegistryCreate,
    ModelRegistryRead,
    ModelRegistryUpdate,
)
from app.schemas import ChatCompletionRequest
from app.services.model_service import _resolve_registry_config
from app.services.registry_service import (
    API_KEY_REQUIRED_PROVIDERS,
    create_model,
    get_model,
    update_model,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Task 1: keyless allowed for openai_compatible (validation) ──

def test_openai_compatible_not_in_api_key_required_providers():
    assert "openai_compatible" not in API_KEY_REQUIRED_PROVIDERS
    # Loosening only — frontier providers still require a key.
    assert {"openai", "azure", "anthropic", "google", "groq"} <= API_KEY_REQUIRED_PROVIDERS


# ── Task 2: keyless at chat runtime (_resolve_registry_config) ──

@pytest.mark.anyio
async def test_resolve_registry_config_noop_without_registry_id():
    req = ChatCompletionRequest(
        provider="openai",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        provider_config={},
    )
    out = await _resolve_registry_config(req)
    assert out is req  # nothing to resolve -> unchanged


@pytest.mark.anyio
async def test_resolve_registry_config_keyless_openai_compatible(monkeypatch):
    mid = str(uuid.uuid4())

    async def _fake_get_session():
        yield object()

    async def _fake_cfg(_session, _model_id):
        return {
            "provider": "openai_compatible",
            "model_name": "llama3.1",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",  # keyless local server
            "provider_config": {"self_hosted": {"is_self_hosted": True}},
            "default_params": {},
        }

    monkeypatch.setattr("app.database.get_session", _fake_get_session)
    monkeypatch.setattr("app.services.registry_service.get_decrypted_config", _fake_cfg)

    req = ChatCompletionRequest(
        provider="openai_compatible",
        model="placeholder",
        messages=[{"role": "user", "content": "hi"}],
        provider_config={"registry_model_id": mid},
    )
    out = await _resolve_registry_config(req)  # must NOT raise
    assert out.provider_config["base_url"] == "http://localhost:11434/v1"
    assert out.provider_config["api_key"] == ""  # keyless passes through (provider -> "not-needed")
    assert out.provider_config["self_hosted"]["is_self_hosted"] is True


@pytest.mark.anyio
async def test_resolve_registry_config_frontier_still_requires_key(monkeypatch):
    mid = str(uuid.uuid4())

    async def _fake_get_session():
        yield object()

    async def _fake_cfg(_session, _model_id):
        return {
            "provider": "openai",
            "model_name": "gpt-4o",
            "base_url": None,
            "api_key": "",  # a frontier model with no key is a misconfiguration
            "provider_config": {},
            "default_params": {},
        }

    monkeypatch.setattr("app.database.get_session", _fake_get_session)
    monkeypatch.setattr("app.services.registry_service.get_decrypted_config", _fake_cfg)

    req = ChatCompletionRequest(
        provider="openai",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        provider_config={"registry_model_id": mid},
    )
    with pytest.raises(ValueError):
        await _resolve_registry_config(req)


# ── Task 5: self_hosted marker round-trips through provider_config (no migration) ──

@pytest.mark.anyio
async def test_self_hosted_marker_roundtrips_through_provider_config(monkeypatch):
    class _Stub:
        key_vault_url = None  # local mode
        encryption_key = "unused-for-keyless"
        key_vault_secret_prefix = "test"

    monkeypatch.setattr("app.services.registry_service.get_settings", lambda: _Stub())

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    marker = {
        "is_self_hosted": True,
        "kind": "slm",
        "fine_tuned": True,
        "base_model": "llama3.1",
        "notes": "ft on invoices",
    }
    async with session_factory() as session:
        created = await create_model(
            session,
            ModelRegistryCreate(
                display_name="FT Llama",
                provider="openai_compatible",
                model_name="llama3.1-ft",
                model_type="llm",
                base_url="http://localhost:8000/v1",
                api_key=None,  # keyless
                show_in=["agent"],
                provider_config={"self_hosted": marker},
            ),
        )
        read = await get_model(session, created.id)

    await engine.dispose()

    assert read is not None
    assert read.provider_config["self_hosted"] == marker  # nested marker preserved
    assert "api_key_encrypted" not in (read.provider_config or {})


# ── Codex should-fix 2: response sanitizer strips api_key_source too ──

def test_from_orm_model_strips_api_key_source():
    row = ModelRegistry(
        display_name="Local",
        provider="openai_compatible",
        model_name="llama",
        model_type="llm",
        api_key_secret_ref=None,
        provider_config={
            "self_hosted": {"is_self_hosted": True, "kind": "slm"},
            "api_key_encrypted": "enc-token",
            "api_key_source": "local_encrypted",
        },
    )
    read = ModelRegistryRead.from_orm_model(row)
    assert "api_key_encrypted" not in (read.provider_config or {})
    assert "api_key_source" not in (read.provider_config or {})  # storage hint not leaked
    assert read.provider_config["self_hosted"]["kind"] == "slm"
    assert read.has_api_key is True


# ── Codex should-fix 1: update validates the TARGET provider, not the pre-update one ──

@pytest.mark.anyio
async def test_update_to_frontier_provider_requires_key(monkeypatch):
    class _Stub:
        key_vault_url = None
        encryption_key = "unused-for-keyless"
        key_vault_secret_prefix = "test"

    monkeypatch.setattr("app.services.registry_service.get_settings", lambda: _Stub())

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        created = await create_model(
            session,
            ModelRegistryCreate(
                display_name="Local",
                provider="openai_compatible",
                model_name="llama",
                model_type="llm",
                base_url="http://localhost:11434/v1",
                api_key=None,  # keyless
            ),
        )
        # Switching a keyless model to a frontier provider WITHOUT a key must be rejected.
        with pytest.raises(HTTPException):
            await update_model(
                session, created.id, ModelRegistryUpdate(provider="openai")
            )

    await engine.dispose()
