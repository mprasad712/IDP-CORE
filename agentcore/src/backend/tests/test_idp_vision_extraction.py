"""Tests for the dynamic multimodal (vision) extraction path.

Covers the pure decision function (text vs vision routing), the bytes-based page
renderer, the vision extractor message assembly, and vision-aware confidence.
"""
import pytest

from agentcore.services.idp.extraction import decide_extraction_input
from agentcore.services.idp.pipeline import PipelineError


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: decide_extraction_input — pure routing decision (runs pre-OCR)
# ─────────────────────────────────────────────────────────────────────────────

# Signature: decide_extraction_input(input_mode, overall_kind, supports_vision: bool, has_ocr_node: bool)
# Auto is CANVAS-DRIVEN: native text -> text; scanned + OCR node -> text (OCR wins); scanned +
# no OCR node + vision model -> vision; scanned + no OCR node + non-vision -> error. Vision
# capability is checkbox-only (a plain bool: True only if the model is marked 'Supports vision').

def test_decide_extraction_input_matrix():
    d = decide_extraction_input
    # auto — a native text layer always wins (OCR node / vision capability irrelevant)
    assert d("auto", "digital", False, False) == "text"
    assert d("auto", "mixed", True, False) == "text"
    # auto — scanned: OCR node WINS over vision
    assert d("auto", "scanned", True, True) == "text"    # OCR node present -> text, even if vision-capable
    assert d("auto", "scanned", False, True) == "text"   # OCR node present, non-vision -> text (OCR)
    assert d("auto", "scanned", True, False) == "vision" # no OCR node + vision model -> vision (skip OCR)
    with pytest.raises(PipelineError):
        d("auto", "scanned", False, False)               # no OCR node + non-vision -> clear error
    # forced text
    assert d("text", "scanned", True, False) == "text"
    # forced vision (checkbox-only: needs supports_vision True)
    assert d("vision", "digital", True, False) == "vision"
    with pytest.raises(PipelineError):
        d("vision", "digital", False, False)             # vision on a model not marked 'Supports vision'
    # text_vision
    assert d("text_vision", "digital", True, False) == "text_vision"
    assert d("text_vision", "scanned", True, True) == "vision"   # no native text -> degrade to vision (+warn)
    with pytest.raises(PipelineError):
        d("text_vision", "digital", False, False)


def test_decide_extraction_input_normalizes_and_defaults():
    assert decide_extraction_input("  VISION ", "digital", True, False) == "vision"
    assert decide_extraction_input(None, "digital", False, False) == "text"   # None mode -> auto -> text


# ─────────────────────────────────────────────────────────────────────────────
# Task 3: render_document_images — bytes-in, list[(png_bytes, mime)] out
# ─────────────────────────────────────────────────────────────────────────────

def _make_pdf_bytes(num_pages: int) -> bytes:
    import fitz
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content")
    data = doc.tobytes()
    doc.close()
    return data


def test_render_document_images_pdf_all_pages():
    from agentcore.services.idp.extraction import render_document_images
    imgs = render_document_images(_make_pdf_bytes(2), "pdf")
    assert len(imgs) == 2
    for b, mime in imgs:
        assert isinstance(b, (bytes, bytearray)) and len(b) > 0
        assert mime == "image/png"


def test_render_document_images_pdf_selected_page_only():
    from agentcore.services.idp.extraction import render_document_images
    imgs = render_document_images(_make_pdf_bytes(3), ".pdf", selected_pages={1})  # 1-based; leading dot tolerated
    assert len(imgs) == 1


def test_render_document_images_image_passthrough():
    import io
    from PIL import Image
    from agentcore.services.idp.extraction import render_document_images
    buf = io.BytesIO(); Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    png = buf.getvalue()
    imgs = render_document_images(png, "png")               # a VALID image passes validation
    assert len(imgs) == 1
    assert imgs[0][1] == "image/png"
    assert imgs[0][0] == png                                # valid image bytes passed through unchanged


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: extract_vision — multimodal message assembly + parse
# ─────────────────────────────────────────────────────────────────────────────

class _FakeVisionLLM:
    """Captures the messages it is invoked with; returns a canned JSON body.

    Has NO ``with_structured_output`` attribute, so extract_vision takes the raw-JSON path
    (which is exactly what we want to assert the message shape against)."""
    def __init__(self, body: str | None = None):
        self.captured = None
        self._body = body or '{"headers": {"invoice_number": {"value": "INV-1", "confidence": 0.9}}, "line_items": []}'

    async def ainvoke(self, messages):
        self.captured = messages

        class _Resp:
            content = self._body
        _Resp.content = self._body
        return _Resp()


@pytest.mark.anyio
async def test_extract_vision_builds_multimodal_message_and_parses():
    from agentcore.services.idp.extraction import extract_vision, render_document_images
    imgs = render_document_images(_make_pdf_bytes(2), "pdf")
    llm = _FakeVisionLLM()
    result = await extract_vision(imgs, llm_model=llm, prompt="Extract invoice number.")

    # message shape: [SystemMessage, HumanMessage(content=[text, image, image])]
    assert len(llm.captured) == 2
    human = llm.captured[1]
    assert isinstance(human.content, list)
    assert human.content[0]["type"] == "text"                                   # leading text block
    image_blocks = [b for b in human.content if b.get("type") == "image_url"]
    assert len(image_blocks) == 2                                               # one per rendered page
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")

    # parsed into the canonical shape save_extraction_results consumes
    assert "headers" in result and "line_items" in result
    assert result["headers"]["invoice_number"]["value"] == "INV-1"


@pytest.mark.anyio
async def test_extract_vision_appends_ocr_text_for_text_vision():
    from agentcore.services.idp.extraction import extract_vision, render_document_images
    imgs = render_document_images(_make_pdf_bytes(1), "pdf")
    llm = _FakeVisionLLM()
    await extract_vision(imgs, llm_model=llm, prompt="Extract.", ocr_text="ACME-OCR-TOKEN-123")
    leading_text = llm.captured[1].content[0]["text"]
    assert "ACME-OCR-TOKEN-123" in leading_text


@pytest.mark.anyio
async def test_extract_vision_uses_field_config_messages_when_given():
    from agentcore.services.idp.extraction import extract_vision, render_document_images
    imgs = render_document_images(_make_pdf_bytes(1), "pdf")
    llm = _FakeVisionLLM()
    await extract_vision(
        imgs, llm_model=llm,
        field_config_messages=("SYS-CONFIG-PROMPT", "USER-CONFIG-INSTRUCTION with fields"),
    )
    system_msg = llm.captured[0]
    assert system_msg.content == "SYS-CONFIG-PROMPT"
    assert "USER-CONFIG-INSTRUCTION" in llm.captured[1].content[0]["text"]


def test_build_compact_extraction_messages_vision_requests_confidence():
    from agentcore.services.idp.prompt_templates import build_compact_extraction_messages_vision

    class _H:
        field_name = "invoice_number"; field_type = "text"; prompt = None; description = None

    class _C:
        column_name = "amount"; column_type = "number"; prompt = None

    system, user = build_compact_extraction_messages_vision([_H()], [_C()])
    # unlike the text compact variant (which suppresses confidence), the vision variant asks for it
    assert "confidence" in system.lower()
    assert "invoice_number" in user
    assert "amount" in user


# ─────────────────────────────────────────────────────────────────────────────
# Task 5: per-field confidence is the MODEL's — on every path, vision or not
# ─────────────────────────────────────────────────────────────────────────────

def test_every_path_now_uses_the_models_confidence_verbatim():
    """Vision used to be the ONLY path that kept the model's number. Now every path does.

    The text path dampened it (`llm_conf x 0.92` with no tokens) or replaced it outright with an OCR
    substring ratio, so an identical extraction scored differently depending on whether the document
    happened to be scanned, digital, or a docx. The file format no longer touches the score.
    """
    from agentcore.services.idp.extraction import _model_confidence

    assert _model_confidence(0.9, "x") == 0.9
    assert _model_confidence(0.42, "x") == 0.42


def test_a_populated_field_with_no_model_confidence_is_unknown_not_a_constant():
    from agentcore.services.idp.extraction import _model_confidence

    assert _model_confidence(None, "x") is None       # model ignored the schema -> unknown, NOT 0.75
    assert _model_confidence("garbage", "x") is None  # unparseable -> unknown
    assert _model_confidence(None, None) == 0.0       # no value at all -> a real 0.0
    assert _model_confidence(0.9, "  ") == 0.0        # blank value -> 0.0 whatever it claims


def test_save_extraction_results_no_longer_takes_vision_mode():
    """`vision_mode` chose how hard to dampen the score. Nothing dampens the score any more.

    Grounding availability is decided by whether `ocr_tokens` is non-empty — a fact, not a mode flag.
    """
    import inspect
    from agentcore.services.idp.extraction import compute_overall_confidence, save_extraction_results

    assert "vision_mode" not in inspect.signature(save_extraction_results).parameters
    assert "ocr_tokens" in inspect.signature(save_extraction_results).parameters
    # ...and the score function cannot even SEE the tokens. It is structurally incapable of blending them.
    assert list(inspect.signature(compute_overall_confidence).parameters) == ["extraction_result"]


# ─────────────────────────────────────────────────────────────────────────────
# Task 7: _supports_vision — capability read + provider deny-list
# ─────────────────────────────────────────────────────────────────────────────

def test_supports_vision_capability_and_denylist():
    from agentcore.services.idp.pipeline import _supports_vision

    class _Reg:
        def __init__(self, provider, caps):
            self.provider = provider
            self.capabilities = caps

    # checkbox-only: vision-capable ONLY when capabilities.supports_vision is exactly True
    assert _supports_vision(None) is False                                         # no model resolved -> not vision
    assert _supports_vision(_Reg("openai", {"supports_vision": True})) is True
    assert _supports_vision(_Reg("openai", {"supports_vision": False})) is False
    assert _supports_vision(_Reg("openai", {})) is False                           # unset -> not vision
    assert _supports_vision(_Reg("openai", None)) is False
    # deny-list: a provider that silently stringifies images is False even if it claims vision
    assert _supports_vision(_Reg("google_genai_vertex", {"supports_vision": True})) is False


# ─────────────────────────────────────────────────────────────────────────────
# Hardening (post-review quick wins): confidence clamp, input validation, error surfacing
# ─────────────────────────────────────────────────────────────────────────────

def test_expand_value_clamps_confidence_to_unit_range():
    from agentcore.services.idp.extraction import _expand_value
    # a model that returns 95 (meaning 95%) or 1.2 / -0.5 must NOT poison routing thresholds
    assert _expand_value({"value": "x", "confidence": 95})[1] == 1.0
    assert _expand_value({"value": "x", "confidence": 1.2})[1] == 1.0
    assert _expand_value({"value": "x", "confidence": -0.5})[1] == 0.0
    assert _expand_value({"value": "x", "confidence": 0.83})[1] == 0.83   # in-range unchanged


def test_out_of_range_model_confidence_is_clamped():
    """A model answering 95 (meaning 95%), 1.5, or -0.2 must never reach the DB's [0,1] CHECK constraint.

    Clamped, deliberately NOT rescaled: 95 -> 1.0, not 0.95. A model that ignores the 0.0–1.0 instruction
    is a model whose confidence we cannot interpret, and inventing 0.95 for it is the same class of lie as
    the old hardcoded 0.75. Clamping fails toward "it claims certainty" — visible and checkable.
    """
    from agentcore.services.idp.extraction import _model_confidence

    assert _model_confidence(1.5, "x") == 1.0
    assert _model_confidence(95.0, "x") == 1.0
    assert _model_confidence(-0.2, "x") == 0.0
    assert _model_confidence(-0.5, "x") == 0.0
    assert _model_confidence(0.83, "x") == 0.83   # in-range untouched


def test_a_confident_hallucination_keeps_its_confidence_and_is_caught_by_grounding():
    """The central invariant of the split. Confidence and grounding must disagree here, or neither works.

    A hallucinated value carries HIGH model confidence — that IS hallucination. Damping the score toward
    zero (the old `llm_conf x 0.38`) both hid the model's claim and mislabelled every correctly reformatted
    date as invented. Keep the claim; report separately that it is not on the page.
    """
    from agentcore.services.idp.extraction import _model_confidence
    from agentcore.services.idp.grounding import Grounder

    tokens = [{"text": "INV-2026-0042", "bounding_box": None, "confidence": 1.0, "page_number": 1}]
    grounder = Grounder(tokens)

    assert _model_confidence(0.99, "INV-2099-9999") == 0.99          # the model is sure...
    assert grounder.check("INV-2099-9999") == (None, False)          # ...and it is not on the page

    assert _model_confidence(0.99, "INV-2026-0042") == 0.99
    assert grounder.check("INV-2026-0042")[1] is True

    # No token stream -> grounding is UNKNOWN, never False. Vision documents must not all route to review.
    assert Grounder([]).check("INV-2099-9999") == (None, None)


def test_render_document_images_rejects_bad_input():
    import pytest as _pt
    from agentcore.services.idp.extraction import render_document_images
    # unsupported file type for vision (e.g. Office doc)
    with _pt.raises(ValueError):
        render_document_images(b"PK\x03\x04 docx bytes", "docx")
    # empty bytes
    with _pt.raises(ValueError):
        render_document_images(b"", "png")
    # corrupt/undecodable image bytes
    with _pt.raises(ValueError):
        render_document_images(b"\x89PNG\r\n\x1a\nGARBAGE", "png")
    # corrupt PDF stream
    with _pt.raises(ValueError):
        render_document_images(b"not really a pdf", "pdf")


def test_vision_error_hint_classifies_provider_errors():
    from agentcore.services.idp.pipeline import _vision_error_hint
    assert "rate" in _vision_error_hint("429 RESOURCE_EXHAUSTED quota").lower()
    assert "large" in _vision_error_hint("413 request entity too large").lower()
    assert "large" in _vision_error_hint("maximum context length exceeded").lower()
    assert _vision_error_hint("some totally unrelated error") == ""   # unknown -> no hint
