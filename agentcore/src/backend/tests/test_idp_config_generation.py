"""Tests for the IDP field-config generation service + endpoint."""

from uuid import uuid4

import pytest

from agentcore.services.idp import config_generation as cg


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ─────────────────────────── normalization (pure) ───────────────────────────
def test_normalize_coerces_types_dedupes_and_orders():
    raw = cg.SuggestedConfig(
        suggested_name="  GST Invoice  ",
        headers=[
            cg._SuggestedHeader(field_name="Invoice No", field_type="string", is_required=True, prompt="the invoice number"),
            cg._SuggestedHeader(field_name="invoice no", field_type="text"),   # dup (case-insensitive) -> dropped
            cg._SuggestedHeader(field_name="Total", field_type="currency"),    # alias 'currency' -> 'number'
            cg._SuggestedHeader(field_name="Notes", field_type="weird"),       # unknown -> 'text'
        ],
        line_items=[
            cg._SuggestedLineItem(column_name="Qty", column_type="number"),
            cg._SuggestedLineItem(column_name="", column_type="text"),          # empty -> dropped
        ],
    )
    out = cg._normalize(raw)
    assert out["suggested_name"] == "GST Invoice"
    assert [h["field_name"] for h in out["headers"]] == ["Invoice No", "Total", "Notes"]
    assert out["headers"][0]["field_type"] == "text" and out["headers"][0]["is_required"] is True
    assert out["headers"][0]["display_order"] == 1 and out["headers"][1]["display_order"] == 2
    assert out["headers"][1]["field_type"] == "number"   # 'currency' alias
    assert out["headers"][2]["field_type"] == "text"     # 'weird' -> text
    assert [li["column_name"] for li in out["line_items"]] == ["Qty"]
    assert out["line_items"][0]["display_order"] == 1


def test_normalize_caps_field_counts():
    raw = cg.SuggestedConfig(
        suggested_name="Big",
        headers=[cg._SuggestedHeader(field_name=f"h{i}") for i in range(60)],
        line_items=[cg._SuggestedLineItem(column_name=f"c{i}") for i in range(60)],
    )
    out = cg._normalize(raw)
    assert len(out["headers"]) == cg._MAX_HEADERS
    assert len(out["line_items"]) == cg._MAX_LINE_ITEMS


# ─────────────────────────── generate (LLM call) ───────────────────────────
class _StructuredLLM:
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        return self._result


class _RawJSONLLM:
    """No with_structured_output; ainvoke returns an object with a .content JSON string."""
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        class _Resp:
            content = self._content
        return _Resp()


@pytest.mark.anyio
async def test_generate_uses_structured_output():
    suggested = cg.SuggestedConfig(
        suggested_name="Purchase Order",
        headers=[cg._SuggestedHeader(field_name="PO Number", field_type="text", is_required=True, prompt="the PO no.")],
        line_items=[cg._SuggestedLineItem(column_name="Item", column_type="text")],
    )
    out = await cg.generate_field_config("a purchase order", None, _StructuredLLM(suggested))
    assert out["suggested_name"] == "Purchase Order"
    assert out["headers"][0]["field_name"] == "PO Number" and out["headers"][0]["is_required"] is True
    assert out["line_items"][0]["column_name"] == "Item"


@pytest.mark.anyio
async def test_generate_falls_back_to_raw_json():
    content = '{"suggested_name":"Receipt","headers":[{"field_name":"Date","field_type":"date"}],"line_items":[]}'
    out = await cg.generate_field_config("a receipt", "sample text", _RawJSONLLM(content))
    assert out["suggested_name"] == "Receipt"
    assert out["headers"][0]["field_name"] == "Date" and out["headers"][0]["field_type"] == "date"


@pytest.mark.anyio
async def test_generate_raises_when_no_headers():
    empty = cg.SuggestedConfig(suggested_name="X", headers=[], line_items=[])
    with pytest.raises(cg.ConfigGenerationError):
        await cg.generate_field_config("nonsense", None, _StructuredLLM(empty))


@pytest.mark.anyio
async def test_generate_raises_on_empty_description():
    with pytest.raises(cg.ConfigGenerationError):
        await cg.generate_field_config("  ", None, _StructuredLLM(cg.SuggestedConfig()))


# ─────────────────────────── endpoint ───────────────────────────
def _mock_user():
    from types import SimpleNamespace
    return SimpleNamespace(id=uuid4(), role="super_admin")


async def _fake_build_llm(session, model_id):
    return object()


@pytest.mark.anyio
async def test_generate_endpoint_returns_draft(monkeypatch):
    from fastapi.testclient import TestClient
    from agentcore.main import create_app
    from agentcore.services.auth.utils import get_current_active_user
    from agentcore.api.idp import idp_rbac
    from agentcore.api.idp import field_configs as fc_api

    async def fake_generate(description, sample_text, llm_model):
        return {"suggested_name": "Invoice",
                "headers": [{"field_name": "Invoice Number", "field_type": "text", "is_required": True, "prompt": "the invoice no", "display_order": 1}],
                "line_items": [{"column_name": "Amount", "column_type": "number", "is_required": False, "prompt": "the amount", "display_order": 1}]}

    monkeypatch.setattr(fc_api, "_build_llm", _fake_build_llm)
    monkeypatch.setattr(fc_api, "generate_field_config", fake_generate)

    app = create_app()
    app.dependency_overrides[get_current_active_user] = _mock_user
    app.dependency_overrides[idp_rbac] = _mock_user
    client = TestClient(app)
    try:
        r = client.post("/api/v1/idp/field-configs/generate",
                        json={"description": "an invoice", "model_id": str(uuid4())})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["suggested_name"] == "Invoice"
        assert body["headers"][0]["field_name"] == "Invoice Number"
        assert body["line_items"][0]["column_name"] == "Amount"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_generate_endpoint_422_on_generation_error(monkeypatch):
    from fastapi.testclient import TestClient
    from agentcore.main import create_app
    from agentcore.services.auth.utils import get_current_active_user
    from agentcore.api.idp import idp_rbac
    from agentcore.api.idp import field_configs as fc_api
    from agentcore.services.idp.config_generation import ConfigGenerationError

    async def fake_generate(description, sample_text, llm_model):
        raise ConfigGenerationError("the model proposed no header fields")

    monkeypatch.setattr(fc_api, "_build_llm", _fake_build_llm)
    monkeypatch.setattr(fc_api, "generate_field_config", fake_generate)

    app = create_app()
    app.dependency_overrides[get_current_active_user] = _mock_user
    app.dependency_overrides[idp_rbac] = _mock_user
    client = TestClient(app)
    try:
        r = client.post("/api/v1/idp/field-configs/generate",
                        json={"description": "gibberish", "model_id": str(uuid4())})
        assert r.status_code == 422, r.text
        assert "header" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
