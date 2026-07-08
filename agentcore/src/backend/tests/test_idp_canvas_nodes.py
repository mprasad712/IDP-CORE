import pytest
from types import SimpleNamespace
from uuid import uuid4
from sqlmodel.ext.asyncio.session import AsyncSession

from agentcore.services.idp.agent_config import resolve_pipeline_config, ResolvedPipelineConfig
from agentcore.services.database.models.idp.config import IdpAgent, IdpAgentRule
from agentcore.services.idp import long_doc
from agentcore.services.idp.pipeline import _route


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Config Parsing tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_resolve_pipeline_config_from_canvas_nodes():
    # Build a mock base agent with custom React-Flow data containing the 5 nodes
    class MockBaseAgent:
        def __init__(self):
            self.id = uuid4()
            self.data = {
                "nodes": [
                    {
                        "data": {
                            "node": {
                                "display_name": "Large Language Model",
                                "template": {
                                    "registry_model": {"value": "gpt-4o | gpt-4o | 11111111-2222-3333-4444-555555555555"}
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "AI Field Extractor",
                                "template": {
                                    "extraction_mode": {"value": "dynamic_prompt"},
                                    "prompt": {"value": "Extract everything."},
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "Chunking Strategy",
                                "template": {
                                    "chunk_size_tokens": {"value": 1500},
                                    "overlap_tokens": {"value": 120},
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "Chunk Aggregator",
                                "template": {
                                    "dedup_strategy": {"value": "merge_all"}
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "Confidence Router",
                                "template": {
                                    "threshold": {"value": 0.85}
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "Rules / Conditions",
                                "template": {
                                    "logic_operator": {"value": "OR"},
                                    "conditions": {"value": '[{"field":"confidence","op":"gte","value":0.9}]'}
                                }
                            }
                        }
                    },
                    {
                        "data": {
                            "node": {
                                "display_name": "Approval Gate",
                                "template": {
                                    "approval_field": {"value": "decision_action"},
                                    "approve_value": {"value": "approved_path"}
                                }
                            }
                        }
                    }
                ]
            }

    base_agent = MockBaseAgent()
    idp_agent = IdpAgent(
        agent_id=base_agent.id,
        extraction_mode="dynamic_prompting",
        default_rule_action="pending_review",
        extra={}
    )

    # Resolve configuration
    cfg: ResolvedPipelineConfig = await resolve_pipeline_config(None, idp_agent, base_agent)

    # Verify Chunking Strategy parsing
    assert cfg.long_doc_enabled is True
    assert cfg.long_doc_max_tokens == 1500
    assert cfg.long_doc_overlap_tokens == 120

    # Verify Chunk Aggregator parsing
    assert cfg.dedup_strategy == "merge_all"

    # Verify Confidence Router parsing
    assert cfg.confidence_threshold == 0.85

    # Verify Rules / Conditions parsing
    assert cfg.rules_operator == "OR"
    assert len(cfg.canvas_rules) == 1
    assert cfg.canvas_rules[0]["field"] == "confidence"

    # Verify Approval Gate parsing
    assert cfg.approval_field == "decision_action"
    assert cfg.approve_value == "approved_path"


# ─────────────────────────────────────────────────────────────────────────────
# 1b. On-node model_id selection (IDPModelDropdown) — highest priority, backward compat
# ─────────────────────────────────────────────────────────────────────────────

_UUID_A = "11111111-1111-1111-1111-111111111111"  # chosen directly on the IDP node
_UUID_B = "22222222-2222-2222-2222-222222222222"  # legacy "Large Language Model" node
_UUID_C = "33333333-3333-3333-3333-333333333333"  # classifier's own model


def _agent_with_nodes(nodes):
    class MockBaseAgent:
        def __init__(self):
            self.id = uuid4()
            self.data = {"nodes": nodes}
    base = MockBaseAgent()
    idp = IdpAgent(
        agent_id=base.id,
        extraction_mode="dynamic_prompting",
        default_rule_action="pending_review",
        extra={},
    )
    return base, idp


def _llm_node(uuid):
    return {"data": {"node": {"display_name": "Large Language Model",
                              "template": {"registry_model": {"value": f"x | x | {uuid}"}}}}}


def _extractor(model_id_value=None):
    tmpl = {"extraction_mode": {"value": "dynamic_prompt"}, "prompt": {"value": "Extract."}}
    if model_id_value is not None:
        tmpl["model_id"] = {"value": model_id_value}
    return {"data": {"node": {"display_name": "AI Field Extractor", "template": tmpl}}}


@pytest.mark.anyio
async def test_extractor_model_id_wins_over_llm_node():
    # Dropdown stores the app-standard "display · badges (provider) | model_name | uuid" string.
    label = f"Local Llama · SLM (openai_compatible) | llama3.1 | {_UUID_A}"
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), _extractor(label)])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.model_id == _UUID_A


@pytest.mark.anyio
async def test_extractor_model_id_accepts_bare_uuid():
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), _extractor(_UUID_A)])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.model_id == _UUID_A


@pytest.mark.anyio
async def test_no_model_id_falls_back_to_llm_node_and_shares_classify():
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), _extractor(None)])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.model_id == _UUID_B            # backward compatible: legacy path still resolves
    assert cfg.classify_model_id == _UUID_B   # classifier shares the extraction model by default


@pytest.mark.anyio
async def test_classifier_node_can_target_its_own_model():
    classifier = {"data": {"node": {"display_name": "Document Classifier",
                                    "template": {"model_id": {"value": _UUID_C}}}}}
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), _extractor(None), classifier])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.model_id == _UUID_B            # extraction uses the LLM node
    assert cfg.classify_model_id == _UUID_C   # classification uses its own model


# ─────────────────────────────────────────────────────────────────────────────
# 1c. Input Mode (text/vision) selection on the AI Field Extractor node
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_input_mode_defaults_to_auto_when_absent():
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), _extractor(None)])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.input_mode == "auto"


@pytest.mark.anyio
async def test_input_mode_read_and_normalized_from_extractor_node():
    extractor = {"data": {"node": {"display_name": "AI Field Extractor",
                                   "template": {"extraction_mode": {"value": "dynamic_prompt"},
                                                "prompt": {"value": "Extract."},
                                                "input_mode": {"value": "Vision"}}}}}  # mixed-case -> normalized
    base, idp = _agent_with_nodes([_llm_node(_UUID_B), extractor])
    cfg = await resolve_pipeline_config(None, idp, base)
    assert cfg.input_mode == "vision"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Text splitting with Overlap tests
# ─────────────────────────────────────────────────────────────────────────────

def test_long_doc_text_splitting_with_overlap():
    # We want to force splitting, so we need a text longer than 4000 characters.
    # Let's repeat a line 100 times to get ~7700 characters.
    single_line = "This is a line of page text that will be repeated to exceed 4000 characters.\n"
    text = single_line * 100
    
    # max_chars = max(4000, 1000 * 4) = 4000.
    parts = long_doc._split_text_by_tokens(text, limit=1000, overlap=100)
    assert len(parts) >= 2
    # Verify that the overlap context is preserved (last characters of part 0 exist in part 1)
    assert parts[0] != parts[1]
    
    # We look for overlapping text content. Since character splitting uses back-scan for newline,
    # the second chunk should contain text from the first chunk due to overlap.
    overlap_found = False
    for line in parts[0].split("\n")[-10:]:
        if line.strip() and line in parts[1]:
            overlap_found = True
            break
    assert overlap_found, "No overlap found between split text parts."


# ─────────────────────────────────────────────────────────────────────────────
# 3. Chunk-merge Deduplication Strategies tests
#    (the AI Field Extractor merges per-chunk extractions itself — no Chunk Aggregator node)
# ─────────────────────────────────────────────────────────────────────────────

def test_chunk_merge_deduplication_strategies():
    results = [
        {
            "headers": {
                "vendor_name": {"value": "Google", "confidence": 0.8},
                "invoice_number": {"value": "INV-100", "confidence": 0.9}
            },
            "line_items": []
        },
        {
            "headers": {
                "vendor_name": {"value": "Google Inc.", "confidence": 0.95},
                "invoice_number": {"value": "INV-100", "confidence": 0.7}
            },
            "line_items": []
        }
    ]

    # Strategy: keep_highest_confidence
    merged_high_conf = long_doc.merge_chunk_extractions(results, strategy="keep_highest_confidence")
    assert merged_high_conf["headers"]["vendor_name"]["value"] == "Google Inc."  # 0.95 conf wins
    assert merged_high_conf["headers"]["invoice_number"]["value"] == "INV-100"
    assert merged_high_conf["headers"]["invoice_number"]["confidence"] == 0.9    # 0.9 conf wins

    # Strategy: keep_first
    merged_first = long_doc.merge_chunk_extractions(results, strategy="keep_first")
    assert merged_first["headers"]["vendor_name"]["value"] == "Google"  # First result value wins
    assert merged_first["headers"]["invoice_number"]["value"] == "INV-100"
    assert merged_first["headers"]["invoice_number"]["confidence"] == 0.9

    # Strategy: merge_all
    merged_all = long_doc.merge_chunk_extractions(results, strategy="merge_all")
    assert merged_all["headers"]["vendor_name"]["value"] == "Google, Google Inc."  # Both concatenated
    assert merged_all["headers"]["invoice_number"]["value"] == "INV-100"  # Identical value not duplicated
    assert merged_all["headers"]["invoice_number"]["confidence"] == 0.9


# ─────────────────────────────────────────────────────────────────────────────
# 4. Rules mapping and routing tests
# ─────────────────────────────────────────────────────────────────────────────

class DummyObject:
    def __init__(self, **kwargs):
        self.field_name = None
        self.column_name = None
        self.extracted_value = None
        self.confidence_score = None
        self.element_type = None
        self.decoded_value = None
        for k, v in kwargs.items():
            setattr(self, k, v)
        if self.field_name and not self.column_name:
            self.column_name = self.field_name


@pytest.mark.anyio
async def test_route_transient_canvas_rules_evaluation():
    # Setup mock config with canvas rules
    cfg = ResolvedPipelineConfig(
        model_id=str(uuid4()),
        extraction_mode="dynamic_prompt",
        prompt=None,
        config_name=None,
        field_config_id=None,
        multimodal_requested=False,
        page_selection_mode="all",
        first_n_pages=1,
        page_range_start=1,
        page_range_end=2,
        rules_operator="AND",
        canvas_rules=[
            {"field": "confidence", "op": "gte", "value": 0.85},
            {"field": "total_amount", "op": "gt", "value": 100}
        ],
        approval_field="rule_action",
        approve_value="auto_approve",
        confidence_threshold=0.8
    )

    class MockSession:
        async def exec(self, query):
            class MockResult:
                def all(self):
                    return [
                        DummyObject(field_name="total_amount", extracted_value="150.00", confidence_score=0.9)
                    ]
            return MockResult()

    session = MockSession()
    doc_id = uuid4()
    job_id = uuid4()
    agent_id = uuid4()

    # Flow trace logger
    class MockFlow:
        def __init__(self):
            self.events = []
        def step(self, name, status, detail="", **fields):
            self.events.append((name, status, detail))

    flow = MockFlow()

    # Evaluate route: overall confidence = 0.9 (gte 0.85) AND total_amount = 150.00 (gt 100) -> auto_approved
    status_and = await _route(session, doc_id, job_id, agent_id, 0.9, cfg, flow)
    assert status_and == "auto_approved"

    # Evaluate route: overall confidence fails (0.80 < 0.85) -> pending_review
    flow_fail = MockFlow()
    status_fail = await _route(session, doc_id, job_id, agent_id, 0.80, cfg, flow_fail)
    assert status_fail == "pending_review"

    # Evaluate rules with logic operator = OR: overall confidence fails (0.80) but total_amount still passes (150.00 gt 100) -> auto_approved
    cfg.rules_operator = "OR"
    flow_or = MockFlow()
    status_or = await _route(session, doc_id, job_id, agent_id, 0.80, cfg, flow_or)
    assert status_or == "auto_approved"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Shared-channel classification routing (Task 5)
#    The classification lives ONLY in the shared IDP working-set snapshot, NOT
#    in the immediate input Message's payload.  effective_payload() must surface
#    it; get_payload() (old code) cannot — that's the regression this covers.
# ─────────────────────────────────────────────────────────────────────────────


class _T5Vertex:
    def __init__(self, snap):
        self._idp_shared_snapshot = snap


class _T5ExecRes:
    def __init__(self, row):
        self._row = row
    def first(self):
        return self._row


class _T5Sess:
    def __init__(self, rows):
        self._rows = list(rows)
    async def exec(self, stmt):
        return _T5ExecRes(self._rows.pop(0) if self._rows else None)


class _T5Scope:
    def __init__(self, sess):
        self._sess = sess
    async def __aenter__(self):
        return self._sess
    async def __aexit__(self, *a):
        return False


@pytest.mark.anyio
async def test_extractor_resolves_config_from_shared_channel(monkeypatch):
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.message import Message
    rows = [SimpleNamespace(name="Invoice", doc_type="Invoice"),
            SimpleNamespace(name="Aadhaar Card", doc_type="Aadhaar Card")]
    monkeypatch.setattr("agentcore.services.deps.session_scope", lambda: _T5Scope(_T5Sess(rows)))
    comp = IDPLLMExtractor()
    comp.config_names = ["Invoice", "Aadhaar Card"]
    comp.document = Message(text="TAX INVOICE", additional_kwargs={"idp": {"document_id": "d1"}})  # NO classification locally
    comp._vertex = _T5Vertex({"classification": {"type": "invoice"}, "document_id": "d1"})          # only in the shared channel
    assert await comp._resolve_config_name_from_classification() == "Invoice"


@pytest.mark.anyio
async def test_extractor_skips_when_shared_classification_matches_no_config(monkeypatch):
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.message import Message
    rows = [SimpleNamespace(name="Invoice", doc_type="Invoice"),
            SimpleNamespace(name="Aadhaar Card", doc_type="Aadhaar Card")]
    monkeypatch.setattr("agentcore.services.deps.session_scope", lambda: _T5Scope(_T5Sess(rows)))
    comp = IDPLLMExtractor()
    comp.config_names = ["Invoice", "Aadhaar Card"]
    comp.document = Message(text="MEMO", additional_kwargs={"idp": {"document_id": "d2"}})
    comp._vertex = _T5Vertex({"classification": {"type": "memo"}})
    assert await comp._resolve_config_name_from_classification() is None   # no doc_type matches "memo" -> skip


# ─────────────────────────────────────────────────────────────────────────────
# 6. Document Splitter node registration
# ─────────────────────────────────────────────────────────────────────────────

def test_document_splitter_is_registered():
    from agentcore.components.IDP import IDPDocumentSplitter  # noqa: F401
    import agentcore.components.IDP as idp
    assert "IDPDocumentSplitter" in idp.__all__
    comp = IDPDocumentSplitter()
    names = {o.name for o in comp.outputs}
    assert names == {"single_document", "split"}


# ─────────────────────────────────────────────────────────────────────────────
# 7. AI Field Extractor — "On unmatched type" control (Task 4b)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_extractor_classifier_unknown_single_config_returns_none():
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.message import Message
    comp = IDPLLMExtractor()
    comp.config_names = ["Invoice"]
    comp.document = Message(text="x", additional_kwargs={"idp": {"classification": {"type": "unknown"}}})
    assert await comp._resolve_config_name_from_classification() is None   # classifier ran, unknown -> no-match


@pytest.mark.anyio
async def test_extractor_no_classifier_single_config_uses_it():
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.message import Message
    comp = IDPLLMExtractor()
    comp.config_names = ["Invoice"]
    comp.document = Message(text="x", additional_kwargs={"idp": {}})   # NO classifier
    assert await comp._resolve_config_name_from_classification() == "Invoice"   # unchanged behavior


@pytest.mark.anyio
async def test_handle_unmatched_skip(monkeypatch):
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.data import Data
    comp = IDPLLMExtractor(); comp.unmatched_action = "skip"
    seen = {}
    async def fake_skip(self, ct, reason=None): seen["ct"] = ct; return Data(data={})
    monkeypatch.setattr(IDPLLMExtractor, "_mark_skipped", fake_skip, raising=True)
    out = await comp._handle_unmatched("medical report", ["Invoice"], True)
    assert isinstance(out, Data) and seen["ct"] == "medical report"


@pytest.mark.anyio
async def test_handle_unmatched_extract_anyway():
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    comp = IDPLLMExtractor(); comp.unmatched_action = "extract_anyway"
    out = await comp._handle_unmatched("medical report", ["Invoice"], True)
    assert out is None                          # proceed to extraction
    assert comp._override_config_name == "Invoice"


@pytest.mark.anyio
async def test_handle_unmatched_drop(monkeypatch):
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.data import Data
    comp = IDPLLMExtractor(); comp.unmatched_action = "drop"
    seen = {}
    async def fake_drop(self, ct): seen["ct"] = ct; return Data(data={})
    monkeypatch.setattr(IDPLLMExtractor, "_drop_document", fake_drop, raising=True)
    out = await comp._handle_unmatched("medical report", ["Invoice"], True)
    assert isinstance(out, Data) and seen["ct"] == "medical report"


@pytest.mark.anyio
async def test_handle_unmatched_no_classifier_always_skips(monkeypatch):
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.data import Data
    comp = IDPLLMExtractor(); comp.unmatched_action = "extract_anyway"   # even extract_anyway...
    seen = {}
    async def fake_skip(self, ct, reason=None): seen["ct"] = ct; return Data(data={})
    monkeypatch.setattr(IDPLLMExtractor, "_mark_skipped", fake_skip, raising=True)
    out = await comp._handle_unmatched("unknown", ["Invoice", "Aadhaar"], False)   # no classifier
    assert isinstance(out, Data) and seen["ct"] == "unknown"   # ...still skips when no classifier ran
