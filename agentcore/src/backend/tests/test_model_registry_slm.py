"""Tests for self-hosted (local LLM / SLM) model registry support.

Covers:
  - Task 4: ModelRegistryRead.from_orm_model must NOT leak provider_config secrets
    (api_key_encrypted / api_key_source) to API responses, and must report has_api_key
    for locally-encrypted keys — while preserving the nested provider_config["self_hosted"]
    marker used by the Local & Self-Hosted Models page + builder dropdown.
  - Task 3: keyless creation is allowed for the openai_compatible provider (self-hosted /
    local servers may be keyless) while frontier providers still require an API key.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentcore.services.database.models.model_registry.model import (
    ModelRegistry,
    ModelRegistryCreate,
    ModelRegistryRead,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Task 3: keyless create allowed for openai_compatible; frontier still requires key ──

@pytest.mark.anyio
async def test_keyless_openai_compatible_passes_create_validation(monkeypatch):
    """A self-hosted/local model may be keyless: URL+name required, key optional."""
    from agentcore.api import model_registry as mr

    async def _ok(_payload):
        return {"success": True}

    monkeypatch.setattr(mr, "test_connection_via_service", _ok)

    body = ModelRegistryCreate(
        display_name="Local Llama",
        provider="openai_compatible",
        model_name="llama3.1",
        model_type="llm",
        base_url="http://localhost:11434/v1",
        api_key=None,
        provider_config={
            "self_hosted": {
                "is_self_hosted": True,
                "kind": "local_llm",
                "fine_tuned": False,
                "base_model": None,
                "notes": None,
            }
        },
    )
    # Must NOT raise "API key is required".
    await mr._validate_model_connection_before_create(body)


@pytest.mark.anyio
async def test_keyless_frontier_provider_still_requires_key():
    from agentcore.api import model_registry as mr

    body = ModelRegistryCreate(
        display_name="GPT",
        provider="openai",
        model_name="gpt-4o",
        model_type="llm",
        api_key=None,
    )
    with pytest.raises(HTTPException):
        await mr._validate_model_connection_before_create(body)


# ── Task 4: provider_config secret must not leak; has_api_key honours local key ──

def test_from_orm_model_strips_encrypted_key_but_keeps_self_hosted_marker():
    row = ModelRegistry(
        display_name="Local Llama",
        provider="openai_compatible",
        model_name="llama3.1",
        model_type="llm",
        base_url="http://localhost:11434/v1",
        api_key_secret_ref=None,  # local-encrypted, not a Key Vault ref
        provider_config={
            "self_hosted": {
                "is_self_hosted": True,
                "kind": "slm",
                "fine_tuned": True,
                "base_model": "llama3.1",
                "notes": "ft on invoices",
            },
            "api_key_encrypted": "gAAAAA-secret-token",
            "api_key_source": "local_encrypted",
        },
    )

    read = ModelRegistryRead.from_orm_model(row)

    assert read.provider_config is not None
    # Secret material / storage hints must never reach the frontend.
    assert "api_key_encrypted" not in read.provider_config
    assert "api_key_source" not in read.provider_config
    # The self-hosted marker (used by the page filter + card badges) is preserved.
    assert read.provider_config["self_hosted"]["kind"] == "slm"
    assert read.provider_config["self_hosted"]["fine_tuned"] is True
    # A locally-encrypted key counts as "has a key" (not just Key Vault refs).
    assert read.has_api_key is True
    # The ORM row itself is untouched (only the response is sanitised).
    assert "api_key_encrypted" in (row.provider_config or {})


def test_from_orm_model_has_api_key_true_for_key_vault_ref():
    row = ModelRegistry(
        display_name="OpenAI GPT",
        provider="openai",
        model_name="gpt-4o",
        model_type="llm",
        api_key_secret_ref="kv-secret-name",
        provider_config={},
    )
    read = ModelRegistryRead.from_orm_model(row)
    assert read.has_api_key is True
