"""Publish-time IDP validation: block publish (either env) unless a Field Configuration + an
active, env-valid LLM are selected. No-op for non-IDP agents. (services save-path untouched.)"""
from uuid import uuid4

import pytest
from fastapi import HTTPException

from agentcore.api.publish import _validate_idp_publish

MID = str(uuid4())


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeModel:
    def __init__(self, environments=None, environment="uat", is_active=True, display_name="TestModel"):
        self.environments = environments
        self.environment = environment
        self.is_active = is_active
        self.display_name = display_name


class FakeSession:
    def __init__(self, model=None):
        self._model = model

    async def get(self, _cls, _id):
        return self._model


def _node(display_name, template):
    return {"data": {"node": {"display_name": display_name, "template": template}}}


def _extractor(config_name="", config_names=None, model_id=""):
    return _node("AI Field Extractor", {
        "config_name": {"value": config_name},
        "config_names": {"value": config_names if config_names is not None else []},
        "model_id": {"value": model_id},
    })


def _llm(uuid=MID):
    return _node("Large Language Model", {"registry_model": {"value": f"Disp | model | {uuid}"}})


def _snap(nodes):
    return {"nodes": nodes, "edges": []}


@pytest.mark.anyio
async def test_non_idp_agent_is_noop():
    # No AI Field Extractor / Processed Docs Output node -> not an IDP agent -> never raises.
    await _validate_idp_publish(_snap([_node("ChatInput", {})]), FakeSession(), "prod")


@pytest.mark.anyio
async def test_missing_field_config_blocks_both_envs():
    snap = _snap([_extractor(config_name="", config_names=[]), _llm()])
    for env in ("uat", "prod"):
        with pytest.raises(HTTPException) as e:
            await _validate_idp_publish(snap, FakeSession(FakeModel()), env)
        assert "Field Configuration" in e.value.detail and env.upper() in e.value.detail


@pytest.mark.anyio
async def test_missing_model_blocks():
    snap = _snap([_extractor(config_name="Invoice")])  # config set, no LLM node
    with pytest.raises(HTTPException) as e:
        await _validate_idp_publish(snap, FakeSession(None), "uat")
    assert "Language Model" in e.value.detail


@pytest.mark.anyio
async def test_uat_only_model_blocks_prod():
    snap = _snap([_extractor(config_name="Invoice"), _llm()])
    with pytest.raises(HTTPException) as e:
        await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["uat"])), "prod")
    assert "registered for" in e.value.detail and "PROD" in e.value.detail


@pytest.mark.anyio
async def test_prod_only_model_blocks_uat():
    snap = _snap([_extractor(config_name="Invoice"), _llm()])
    with pytest.raises(HTTPException) as e:
        await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["prod"])), "uat")
    assert "UAT" in e.value.detail


@pytest.mark.anyio
async def test_both_envs_model_passes():
    snap = _snap([_extractor(config_name="Invoice"), _llm()])
    await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["uat", "prod"])), "prod")
    await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["uat", "prod"])), "uat")


@pytest.mark.anyio
async def test_inactive_model_blocks():
    snap = _snap([_extractor(config_name="Invoice"), _llm()])
    with pytest.raises(HTTPException) as e:
        await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["uat", "prod"], is_active=False)), "uat")
    assert "inactive" in e.value.detail


@pytest.mark.anyio
async def test_multi_config_satisfies_requirement():
    # config_names (multi-type) alone satisfies the Field Configuration requirement.
    snap = _snap([_extractor(config_name="", config_names=["Invoice", "PAN"]), _llm()])
    await _validate_idp_publish(snap, FakeSession(FakeModel(environments=["uat"])), "uat")
