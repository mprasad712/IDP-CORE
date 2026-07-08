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
