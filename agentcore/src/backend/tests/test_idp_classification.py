import pytest
from uuid import uuid4
from sqlmodel import select

from agentcore.services.deps import session_scope
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.model_registry.model import ModelRegistry
from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration, IdpFieldConfigHeader
from agentcore.services.database.models.idp.documents import IdpDocument, IdpDocumentClassification
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.idp.classification import classify_and_persist, classify_via_llm, ClassificationResult
from agentcore.services.idp import classification


@pytest.fixture
def anyio_backend():
    return "asyncio"


class MockLLM:
    def __init__(self, structured_result):
        self.structured_result = structured_result

    def with_structured_output(self, schema_cls):
        return self

    async def ainvoke(self, messages, **kwargs):
        return self.structured_result


@pytest.fixture(autouse=True)
def mock_llm_builder(monkeypatch):
    # Mock settings so settings service returns a valid model service url
    from agentcore.services.deps import get_settings_service
    settings = get_settings_service().settings
    settings.model_service_url = "http://localhost:8001"
    settings.model_service_api_key = "test"


async def _setup_test_agent_and_doc(session, model_id):
    agent_id = uuid4()
    doc_id = uuid4()
    org_id = uuid4()
    user_id = uuid4()
    unique_suffix = uuid4().hex[:8]

    # Create User
    user = User(
        id=user_id,
        username=f"class_test_{unique_suffix}@test.com",
        email=f"class_test_{unique_suffix}@test.com",
        password="testpassword",
        is_active=True,
        is_superuser=False,
        role="idp_configurator",
    )
    session.add(user)
    await session.flush()

    # Create Organization
    org = Organization(
        id=org_id,
        name=f"Classification Test Org {unique_suffix}",
        owner_user_id=user_id,
        created_by=user_id,
    )
    session.add(org)
    await session.flush()

    # Create base Agent containing graph data
    graph = {
        "nodes": [
            {
                "id": "extractor",
                "data": {
                    "node": {
                        "display_name": "AI Field Extractor",
                        "template": {"extraction_mode": {"value": "dynamic_prompt"}, "prompt": {"value": "Extract"}, "config_name": {"value": ""}}
                    }
                }
            },
            {
                "id": "model",
                "data": {
                    "node": {
                        "display_name": "Large Language Model",
                        "template": {"registry_model": {"value": f"GPT | gpt-4o | {model_id}"}}
                    }
                }
            }
        ]
    }
    
    base_agent = Agent(id=agent_id, name="Test Classifier Agent", data=graph)
    session.add(base_agent)
    await session.flush()

    # Create IDP Agent config
    idp_agent = IdpAgent(id=agent_id, agent_id=agent_id, extraction_mode="dynamic_prompting")
    session.add(idp_agent)
    await session.flush()

    # Create Document
    doc = IdpDocument(
        id=doc_id,
        agent_id=agent_id,
        original_filename="invoice.pdf",
        file_path="mock/invoice.pdf",
        file_type="pdf",
        file_size_bytes=100,
        source="upload",
        status="queued",
        uploaded_by=user_id
    )
    session.add(doc)
    await session.commit()
    return agent_id, doc_id, org_id


async def _cleanup(agent_id, doc_id, org_id):
    async with session_scope() as session:
        classifications = (await session.exec(select(IdpDocumentClassification).where(IdpDocumentClassification.document_id == doc_id))).all()
        for c in classifications:
            await session.delete(c)
        doc = await session.get(IdpDocument, doc_id)
        if doc: await session.delete(doc)
        ia = await session.get(IdpAgent, agent_id)
        if ia: await session.delete(ia)
        ba = await session.get(Agent, agent_id)
        if ba: await session.delete(ba)
        
        # Cleanup any configurations generated for the org
        configs = (await session.exec(select(IdpFieldConfiguration).where(IdpFieldConfiguration.org_id == org_id))).all()
        for cfg in configs:
            await session.delete(cfg)

        # Cleanup Organization and User
        org = await session.get(Organization, org_id)
        if org:
            owner_id = org.owner_user_id
            await session.delete(org)
            user = await session.get(User, owner_id)
            if user:
                await session.delete(user)
                
        await session.commit()


@pytest.mark.anyio
async def test_classification_autoselect(monkeypatch):
    model_id = uuid4()
    
    # Pre-add ModelRegistry record so validation passes
    async with session_scope() as session:
        reg = ModelRegistry(id=model_id, provider="openai", model="gpt-4o", model_type="chat", is_active=True, approval_status="approved", display_name="gpt-4o", model_name="gpt-4o")
        session.add(reg)
        
        # Ensure a global template for Invoice exists in DB (seeded normally, but let's be sure)
        invoice_template = (await session.exec(select(IdpFieldConfiguration).where(IdpFieldConfiguration.name == "Invoice", IdpFieldConfiguration.is_template == True))).first()
        if not invoice_template:
            invoice_template = IdpFieldConfiguration(id=uuid4(), name="Invoice", is_template=True, is_active=True)
            session.add(invoice_template)
            header = IdpFieldConfigHeader(id=uuid4(), config_id=invoice_template.id, field_name="invoice_number", field_type="text", display_order=1)
            session.add(header)
        await session.commit()

        agent_id, doc_id, org_id = await _setup_test_agent_and_doc(session, model_id)

    # Mock LLM response high confidence matching template "Invoice"
    mock_result = ClassificationResult(
        predicted_type="Invoice",
        confidence=0.95,
        candidates={"Invoice": 0.95, "Receipt": 0.05}
    )
    
    # Mock LLM building
    monkeypatch.setattr("agentcore.services.model_service_client.MicroserviceChatModel", lambda **k: MockLLM(mock_result))

    try:
        async with session_scope() as session:
            res = await classify_and_persist(
                session=session,
                document_id=doc_id,
                merged_text="INV-100 Invoice Tesco Total 100",
                org_id=org_id,
                auto_select=True,
                threshold=0.8
            )

        assert res["predicted_type"] == "Invoice"
        assert res["confidence"] == 0.95
        assert res["selected_config_id"] is not None

        # Check DB records
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            assert doc.predicted_type == "Invoice"

            cls_row = (await session.exec(select(IdpDocumentClassification).where(IdpDocumentClassification.document_id == doc_id))).first()
            assert cls_row is not None
            assert cls_row.predicted_type == "Invoice"
            assert cls_row.selected_config_id == res["selected_config_id"]
            assert cls_row.is_selected is True
            assert cls_row.candidates == {"Invoice": 0.95, "Receipt": 0.05}

            # Check configuration created for the org
            org_config = await session.get(IdpFieldConfiguration, res["selected_config_id"])
            assert org_config is not None
            assert org_config.org_id == org_id
            assert org_config.is_template is False
    finally:
        await _cleanup(agent_id, doc_id, org_id)
        async with session_scope() as session:
            db_reg = await session.get(ModelRegistry, model_id)
            if db_reg:
                await session.delete(db_reg)
                await session.commit()


@pytest.mark.anyio
async def test_classification_low_conf(monkeypatch):
    model_id = uuid4()
    
    async with session_scope() as session:
        reg = ModelRegistry(id=model_id, provider="openai", model="gpt-4o", model_type="chat", is_active=True, approval_status="approved", display_name="gpt-4o", model_name="gpt-4o")
        session.add(reg)
        await session.commit()
        agent_id, doc_id, org_id = await _setup_test_agent_and_doc(session, model_id)

    # Mock LLM response with low confidence (below 0.8 threshold)
    mock_result = ClassificationResult(
        predicted_type="Invoice",
        confidence=0.5,
        candidates={"Invoice": 0.5, "Receipt": 0.3}
    )
    monkeypatch.setattr("agentcore.services.model_service_client.MicroserviceChatModel", lambda **k: MockLLM(mock_result))

    try:
        async with session_scope() as session:
            res = await classify_and_persist(
                session=session,
                document_id=doc_id,
                merged_text="Some text",
                org_id=org_id,
                auto_select=True,
                threshold=0.8
            )

        assert res["predicted_type"] == "Invoice"
        assert res["selected_config_id"] is None

        async with session_scope() as session:
            cls_row = (await session.exec(select(IdpDocumentClassification).where(IdpDocumentClassification.document_id == doc_id))).first()
            assert cls_row.is_selected is False
            assert cls_row.selected_config_id is None
    finally:
        await _cleanup(agent_id, doc_id, org_id)
        async with session_scope() as session:
            db_reg = await session.get(ModelRegistry, model_id)
            if db_reg:
                await session.delete(db_reg)
                await session.commit()


@pytest.mark.anyio
async def test_classification_clamps_out_of_range_confidence(monkeypatch):
    """An LLM confidence > 1.0 must be clamped to 1.0 (the DB CHECK is 0..1), not raise."""
    model_id = uuid4()
    async with session_scope() as session:
        reg = ModelRegistry(id=model_id, provider="openai", model="gpt-4o", model_type="chat",
                            is_active=True, approval_status="approved", display_name="gpt-4o", model_name="gpt-4o")
        session.add(reg)
        await session.commit()
        agent_id, doc_id, org_id = await _setup_test_agent_and_doc(session, model_id)

    mock_result = ClassificationResult(predicted_type="Invoice", confidence=1.5, candidates={"Invoice": 1.5})
    monkeypatch.setattr("agentcore.services.model_service_client.MicroserviceChatModel", lambda **k: MockLLM(mock_result))

    try:
        async with session_scope() as session:
            res = await classify_and_persist(
                session=session, document_id=doc_id, merged_text="INV-1 Invoice",
                org_id=org_id, auto_select=False, threshold=0.8,
            )
        assert res["confidence"] == 1.0  # clamped
        async with session_scope() as session:
            row = (await session.exec(select(IdpDocumentClassification).where(IdpDocumentClassification.document_id == doc_id))).first()
            assert row is not None and float(row.confidence) == 1.0  # persisted without violating the CHECK
    finally:
        await _cleanup(agent_id, doc_id, org_id)
        async with session_scope() as session:
            db_reg = await session.get(ModelRegistry, model_id)
            if db_reg:
                await session.delete(db_reg)
                await session.commit()


# ---------------------------------------------------------------------------
# Pure unit tests for classify_via_llm — no DB, no network
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeLLMPlainOnly:
    """structured output always fails -> forces the plain ainvoke + JSON-parse fallback path."""
    def __init__(self, content):
        self._content = content

    def with_structured_output(self, schema, include_raw=False):
        raise RuntimeError("structured output unavailable")

    async def ainvoke(self, messages):
        return _FakeResponse(self._content)


class _FakeStructured:
    def __init__(self, payload): self._p = payload
    async def ainvoke(self, messages): return {"parsed": self._p, "raw": None}


class _FakeLLM:
    """Mimics a model whose structured output only works WITH include_raw=True."""
    def __init__(self, payload): self._p = payload
    def with_structured_output(self, schema, include_raw=False):
        if not include_raw:
            raise TypeError("include_raw required")   # forces the working branch
        return _FakeStructured(self._p)


@pytest.mark.anyio
async def test_classify_via_llm_uses_include_raw_and_returns_type():
    llm = _FakeLLM(ClassificationResult(predicted_type="Invoice", confidence=0.9, candidates={"Invoice": 0.9}))
    result = await classify_via_llm(llm, ["Invoice", "Aadhaar Card"], "TAX INVOICE INV-2026-0042")
    assert result.predicted_type == "Invoice"
    assert result.confidence == pytest.approx(0.9)


@pytest.mark.anyio
async def test_classify_via_llm_json_fallback():
    from agentcore.services.idp.classification import classify_via_llm
    fenced = '```json\n{"predicted_type": "Invoice", "confidence": 0.8, "candidates": {}}\n```'
    result = await classify_via_llm(_FakeLLMPlainOnly(fenced), ["Invoice", "Aadhaar Card"], "TAX INVOICE")
    assert result.predicted_type == "Invoice"
    assert result.confidence == pytest.approx(0.8)


@pytest.mark.anyio
async def test_classify_via_llm_sentinel_on_all_failures():
    from agentcore.services.idp.classification import classify_via_llm
    # structured output raises AND the plain response is non-JSON -> must return the sentinel, never raise
    result = await classify_via_llm(_FakeLLMPlainOnly("this is not json at all"), ["Invoice"], "some text")
    assert result.predicted_type == "unknown"
    assert result.confidence == 0.0


@pytest.mark.anyio
async def test_node_invoke_llm_delegates_to_robust_helper(monkeypatch):
    from agentcore.components.IDP.document_classifier import IDPDocumentClassifier
    from agentcore.services.idp.classification import ClassificationResult
    seen = {}
    async def fake(llm_model, doc_types, text, *, descriptions=None, timeout=None):
        seen["descriptions"] = descriptions
        return ClassificationResult(predicted_type="Invoice", confidence=0.9, candidates={"Invoice": 0.9})
    monkeypatch.setattr("agentcore.services.idp.classification.classify_via_llm", fake, raising=True)
    comp = IDPDocumentClassifier()
    comp.document_types = ["Invoice", "Aadhaar Card"]
    result = await comp._invoke_llm(object(), doc_types=["Invoice", "Aadhaar Card"], text="TAX INVOICE",
                                    descriptions={"Invoice": "a tax invoice"})
    assert result.predicted_type == "Invoice"
    assert result.confidence == pytest.approx(0.9)
    assert seen["descriptions"] == {"Invoice": "a tax invoice"}   # descriptions reach the helper


@pytest.mark.anyio
async def test_classify_via_llm_includes_descriptions_in_prompt():
    from agentcore.services.idp.classification import classify_via_llm, ClassificationResult
    captured = {}
    class _CapturingLLM:
        def with_structured_output(self, schema, include_raw=False):
            if not include_raw:
                raise TypeError("need include_raw")
            class _S:
                async def ainvoke(self, messages):
                    captured["prompt"] = messages[0].content
                    return {"parsed": ClassificationResult(predicted_type="Invoice", confidence=0.9, candidates={}), "raw": None}
            return _S()

        async def ainvoke(self, messages):
            raise RuntimeError("should not reach plain path")
    await classify_via_llm(_CapturingLLM(), ["Invoice"], "some text", descriptions={"Invoice": "a commercial tax invoice"})
    assert "a commercial tax invoice" in captured["prompt"]


@pytest.mark.anyio
async def test_classify_via_llm_carries_reasoning_from_json():
    from agentcore.services.idp.classification import classify_via_llm
    fenced = '```json\n{"predicted_type": "Invoice", "confidence": 0.7, "candidates": {}, "reasoning": "has GSTIN and totals"}\n```'
    result = await classify_via_llm(_FakeLLMPlainOnly(fenced), ["Invoice"], "x")
    assert result.reasoning == "has GSTIN and totals"


def test_tag_message_puts_classification_in_idp_payload():
    from agentcore.components.IDP.document_classifier import IDPDocumentClassifier
    from agentcore.services.idp.graph_native.payload import get_payload
    from agentcore.schema.message import Message

    comp = IDPDocumentClassifier()
    src = Message(text="TAX INVOICE", additional_kwargs={"idp": {"document_id": "d1"}})
    out = comp._tag_message(src, "Invoice", 0.9, "matches invoice layout")
    payload = get_payload(out)
    assert payload.get("predicted_type") == "Invoice"
    assert (payload.get("classification") or {}).get("type") == "Invoice"   # rides in idp, not top-level
    assert payload.get("document_id") == "d1"                                # envelope preserved
