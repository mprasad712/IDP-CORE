"""Tests for hybrid multi-doc boundary detection (rules + optional LLM, with per-segment types)."""
import pytest
from agentcore.services.idp import splitting


# ── _boundaries_from_page_texts (rules-only, from brief — UNCHANGED) ─────────


def test_boundaries_from_page_texts_page_one_reset_is_strong():
    # page 2 says "Page 1 of 2" -> a strong reset boundary; all pages >= 10 chars
    page_texts = {
        0: "TAX INVOICE Total amount 100",
        1: "continued line items for order",
        2: "DELIVERY NOTE Page 1 of 2 here",
        3: "delivery items list continues",
    }
    boundaries, has_strong, _ = splitting._boundaries_from_page_texts(page_texts, page_count=4)
    assert boundaries == [(0, 1), (2, 3)]
    assert has_strong is True


def test_boundaries_from_page_texts_header_only_is_hint_not_split():
    # a long single doc whose page 2 heading reuses "invoice" must NOT be split by the rules alone
    page_texts = {
        0: "cover letter body text here",
        1: "more body text continues here",
        2: "invoice summary section follows",
    }
    boundaries, has_strong, has_hint = splitting._boundaries_from_page_texts(page_texts, page_count=3)
    assert boundaries == [(0, 2)]       # ONE document — header keyword did NOT split
    assert has_strong is False
    assert has_hint is True             # but it flags for LLM escalation


# ── detect_boundaries_hybrid (DEFINITIVE dispatch versions — return tuple) ───


@pytest.mark.anyio
async def test_hybrid_rules_only_strong_split_no_model():
    # 'Page 1' on page 2 = strong signal; all pages >= 10 chars
    page_texts = {
        0: "TAX INVOICE number one two",
        1: "line items continue on here",
        2: "PAN CARD Page 1 identity id",
        3: "card details continue on here",
    }
    boundaries, seg_types = await splitting.detect_boundaries_hybrid(page_texts, [], llm_model=None, page_count=4)
    assert boundaries == [(0, 1), (2, 3)]
    assert seg_types is None


@pytest.mark.anyio
async def test_hybrid_no_model_header_hint_does_not_false_split():
    # header hint only, NO strong signal, NO model -> must stay a single document
    page_texts = {0: "cover letter body text", 1: "invoice section body text"}
    boundaries, _ = await splitting.detect_boundaries_hybrid(page_texts, [], llm_model=None, page_count=2)
    assert boundaries == [(0, 1)]             # stays a single document


@pytest.mark.anyio
async def test_hybrid_escalates_to_llm_and_returns_types():
    page_texts = {0: "acme invoice body here", 1: "medical report body no markers"}

    class _FakeLLM:
        async def ainvoke(self, messages):
            class _R:
                content = '{"segments": [{"start_page": 1, "type": "Invoice"}, {"start_page": 2, "type": "Medical Report"}]}'
            return _R()

    boundaries, seg_types = await splitting.detect_boundaries_hybrid(page_texts, [], _FakeLLM(), page_count=2)
    assert boundaries == [(0, 0), (1, 1)]
    assert seg_types == ["Invoice", "Medical Report"]


@pytest.mark.anyio
async def test_hybrid_falls_back_when_llm_fails():
    page_texts = {0: "body content a here", 1: "body content b here"}

    class _BoomLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("llm down")

    boundaries, seg_types = await splitting.detect_boundaries_hybrid(page_texts, [], _BoomLLM(), page_count=2)
    assert boundaries == [(0, 1)]            # conservative single
    assert seg_types is None


# ── textless (image-only / scanned) pages: never dropped, never trusted blindly ──


def test_boundaries_partition_covers_trailing_textless_pages():
    # pages 4-6 are image-only scans (no text layer) — they must stay inside a segment,
    # not be trimmed away (this exact shape previously dropped PAN/medical/Aadhaar pages)
    page_texts = {
        0: "",                                  # scanned form, no text layer
        1: "INVOICE No INV-1 Acme Ltd totals",
        2: "invoice line items continue here",
        3: "Sliced Invoice Page 1/1 total due",
        4: "", 5: "", 6: "",                    # scanned PAN / medical / Aadhaar
    }
    boundaries, _, _ = splitting._boundaries_from_page_texts(page_texts, page_count=7)
    covered = sorted(p for s, e in boundaries for p in range(s, e + 1))
    assert covered == list(range(7))            # every page is covered


@pytest.mark.anyio
async def test_hybrid_textless_pages_break_rules_confidence_and_escalate():
    # strong signals exist among the texted pages, but image-only pages exist too ->
    # the rules must NOT be trusted; the LLM (which got the page images) decides
    page_texts = {
        0: "",
        1: "INVOICE No INV-1 Acme Ltd totals",
        2: "invoice line items continue here",
        3: "Sliced Invoice Page 1/1 total due",
        4: "", 5: "", 6: "",
    }

    class _VisionLLM:
        async def ainvoke(self, messages):
            class _R:
                content = (
                    '{"segments": [{"start_page": 1, "type": "unknown"}, {"start_page": 2, "type": "Invoice"},'
                    ' {"start_page": 4, "type": "Invoice"}, {"start_page": 5, "type": "PAN"},'
                    ' {"start_page": 6, "type": "Medical Report"}, {"start_page": 7, "type": "Aadhaar"}]}'
                )
            return _R()

    images = [(i, b"png-bytes", "image/png") for i in (0, 4, 5, 6)]
    boundaries, seg_types = await splitting.detect_boundaries_hybrid(page_texts, images, _VisionLLM(), page_count=7)
    assert boundaries == [(0, 0), (1, 2), (3, 3), (4, 4), (5, 5), (6, 6)]
    assert seg_types == ["unknown", "Invoice", "Invoice", "PAN", "Medical Report", "Aadhaar"]
    covered = sorted(p for s, e in boundaries for p in range(s, e + 1))
    assert covered == list(range(7))


@pytest.mark.anyio
async def test_hybrid_vision_failure_ocrs_textless_pages_then_retries_text_only():
    # text-only model rejects image content -> hybrid OCRs the textless pages and retries text-only
    page_texts = {0: "INVOICE No INV-9 Acme totals", 1: ""}
    calls = {"n": 0}

    class _TextOnlyLLM:
        async def ainvoke(self, messages):
            calls["n"] += 1
            content = messages[-1].content
            if isinstance(content, list) and any(part.get("type") == "image_url" for part in content):
                raise RuntimeError("model does not support images")
            class _R:
                content = '{"segments": [{"start_page": 1, "type": "Invoice"}, {"start_page": 2, "type": "Aadhaar"}]}'
            return _R()

    async def _fake_ocr(pages):
        assert pages == [1]
        return {1: "GOVERNMENT OF INDIA AADHAAR 9303 4271 3967"}

    images = [(1, b"jpeg-bytes", "image/jpeg")]
    boundaries, seg_types = await splitting.detect_boundaries_hybrid(
        page_texts, images, _TextOnlyLLM(), page_count=2, ocr_pages=_fake_ocr,
    )
    assert calls["n"] == 2                       # vision attempt + text-only retry
    assert boundaries == [(0, 0), (1, 1)]
    assert seg_types == ["Invoice", "Aadhaar"]


@pytest.mark.anyio
async def test_hybrid_no_model_uses_ocr_then_strong_rules():
    # no LLM at all: OCR restores the scanned page's text; a 'Page 1' reset then splits by rules
    page_texts = {0: "TAX INVOICE number one two", 1: ""}

    async def _fake_ocr(pages):
        return {1: "PAN CARD Page 1 identity details here"}

    boundaries, seg_types = await splitting.detect_boundaries_hybrid(
        page_texts, [], None, page_count=2, ocr_pages=_fake_ocr,
    )
    assert boundaries == [(0, 0), (1, 1)]
    assert seg_types is None


@pytest.mark.anyio
async def test_hybrid_everything_unavailable_stays_single_child():
    # no model, OCR fails -> one child covering ALL pages (processed downstream; never dropped)
    page_texts = {0: "TAX INVOICE number one two", 1: "", 2: ""}

    async def _broken_ocr(pages):
        raise RuntimeError("paddle not installed")

    boundaries, seg_types = await splitting.detect_boundaries_hybrid(
        page_texts, [], None, page_count=3, ocr_pages=_broken_ocr,
    )
    assert boundaries == [(0, 2)]
    assert seg_types is None
