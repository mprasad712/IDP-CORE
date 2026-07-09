"""Grounding: is the extracted value actually in the document? A boolean, never a score.

Every case here is a bug that the OCR-substring *scorer* it replaced got wrong. The scorer returned a float
derived from how much of a token the value covered, and that float became `confidence_score`:

    value                token(s)                        old score   why it was wrong
    ─────────────────────────────────────────────────────────────────────────────────────────────────
    042022               "Invoice Number: 042022" (docx)  0.746       ratio = 6/22; a PERFECT extraction
    042022               "042022"                 (pdf)   1.000       same value, same document, ×1.34
    2022-04-30           "30 April 2022"                  0.285       obeyed "dates as YYYY-MM-DD"
    ELLINGTON WOOD DECOR 3 word tokens                    0.776       word-overlap fallback
    1 (quantity)         anything containing "1"          0.700+      substring of "2511.54"
"""

import pytest

from agentcore.services.idp.grounding import Grounder, grounding_summary, normalize, value_variants


def _tok(text, x=0, boxed=True, conf=1.0, page=1):
    box = [[x, 0], [x + 8, 0], [x + 8, 9], [x, 9]] if boxed else None
    return {"text": text, "bounding_box": box, "confidence": conf, "page_number": page}


def _words(sentence, boxed=True):
    """One token per word — what `extract_native_text` emits for a digital PDF, and PaddleOCR for a scan."""
    return [_tok(w, x=i * 10, boxed=boxed) for i, w in enumerate(sentence.split())]


def _paragraphs(*lines):
    """One token per paragraph, no bbox — what `_office_docx` emits."""
    return [_tok(line, boxed=False) for line in lines]


DOC = "Invoice Number : 042022 Date 30 April 2022 Supplier ELLINGTON WOOD DECOR Qty 1 Total $ 2,511.54"


# ───────────────────────── the file format must not decide the answer ─────────────────────────
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("042022", True),
        ("2022-04-30", True),                 # ISO, reformatted from "30 April 2022"
        ("ELLINGTON WOOD DECOR", True),       # spans three word tokens
        ("2511.54", True),                    # split across "2," and "511.54"
        ("1", True),                          # quantity
        ("INV-2099-9999", False),             # hallucination
        ("2024-01-01", False),                # a real date, but not THIS document's
        ("999", False),
    ],
)
def test_word_tokens_and_paragraph_tokens_agree(value, expected):
    """A digital PDF and a docx of the same invoice must ground identically.

    This is the whole reason grounding is a boolean. The scorer divided by token length, so coarser
    tokenization meant lower confidence for identical extracted values.
    """
    pdf = Grounder(_words(DOC))
    docx = Grounder(_paragraphs("Invoice Number: 042022", "Date: 30 April 2022",
                                "Supplier: ELLINGTON WOOD DECOR", "Qty: 1  Total: $2,511.54"))
    assert pdf.check(value)[1] is expected
    assert docx.check(value)[1] is expected


# ───────────────────────── tri-state, and the None case is load-bearing ─────────────────────────
def test_no_token_stream_means_unknown_not_ungrounded():
    """The vision path reads pixels; there is nothing to check against. Every field would be "not found"."""
    g = Grounder([])
    assert g.available is False
    assert g.check("042022") == (None, None)
    assert g.check("INV-2099-9999") == (None, None)


def test_an_absent_value_is_never_checked():
    g = Grounder(_words(DOC))
    assert g.check(None) == (None, None)
    assert g.check("") == (None, None)
    assert g.check("   ") == (None, None)


def test_whitespace_only_tokens_do_not_ground_everything():
    assert Grounder([_tok("   "), _tok("")]).available is False


# ───────────────────────── normalization ─────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  ACME  Corp ", "acme corp"),
        ("$2,511.54", "2511.54"),
        ("£600.00", "600.00"),
        ("₹1,00,000", "100000"),
        ("Total:\u00a0600", "total:600"),   # U+00A0 is a thousands separator -> stripped entirely
        ("Total: 600", "total: 600"),        # ...an ordinary space is a word gap -> kept
        ("2\u00a0511.54", "2511.54"),        # fr/de thousands grouping
    ],
)
def test_normalize_strips_currency_and_thousands_separators(raw, expected):
    assert normalize(raw) == expected


def test_iso_dates_expand_to_the_ways_a_document_actually_prints_them():
    variants = value_variants("2022-04-30")
    for spelling in ("30 april 2022", "april 30 2022", "30/04/2022", "04/30/2022", "30.04.2022"):
        assert spelling in variants, f"{spelling!r} missing from {variants}"


def test_numeric_values_expand_across_trailing_zero_spellings():
    assert "600" in value_variants("600.00")
    assert "600.00" in value_variants("600")


def test_a_non_date_string_is_not_mangled_into_date_variants():
    assert value_variants("INV-2026-0042") == ["inv-2026-0042"]


# ───────────────────────── the boundary guard ─────────────────────────
def test_a_number_does_not_ground_against_a_longer_number():
    """`1` must not match inside `2511.54`, and `600` must not match inside `1600.00`.

    Bare substring matching grounded every short numeric field against any number on the page, which would
    make the hallucination signal useless for exactly the fields (quantities, amounts) that matter most.
    """
    g = Grounder(_words("Total 2511.54 and 1600.00"))
    assert g.check("1")[1] is False
    assert g.check("600")[1] is False
    assert g.check("2511.54")[1] is True
    assert g.check("1600.00")[1] is True


def test_a_number_DOES_ground_against_a_currency_prefix():
    """Only digits and `.` are barriers. `600.00` must still match `GBP600.00` — that is how invoices print."""
    assert Grounder(_words("Total GBP600.00")).check("600.00")[1] is True
    assert Grounder(_words("Total GBP600.00")).check("GBP")[1] is True


def test_a_word_grounds_as_a_plain_substring():
    assert Grounder(_words("supplier:ACME")).check("ACME")[1] is True


# ───────────────────────── source_location ─────────────────────────
def test_the_bounding_box_is_the_union_of_the_values_own_tokens():
    """A multi-word value must highlight the whole value.

    The scorer returned the bbox of whichever token matched first, so a five-line `bill_to_address`
    highlighted the single word "Ellington" — the observed behaviour in document 7e0a275e.
    """
    g = Grounder(_words("Supplier ELLINGTON WOOD DECOR Ltd"))
    loc, grounded = g.check("ELLINGTON WOOD DECOR")
    assert grounded is True
    (x0, _y0), _tr, (x1, y1), _bl = loc["bounding_box"]
    assert (x0, x1, y1) == (10.0, 38.0, 9.0)   # tokens 1..3, not token 0 ("Supplier")


def test_the_bounding_box_covers_the_value_not_its_label():
    """Shortest span wins — the search must widen only after every narrower window has failed.

    A plain left-to-right scan finds `042022` inside the JOINED run "invoice number 042022" starting at
    token 0, and boxes all three words. The value lives in token 2, alone.
    """
    g = Grounder(_words("Invoice Number 042022"))   # x = 0, 10, 20
    loc, _ = g.check("042022")
    assert loc["bounding_box"][0][0] == 20.0, "the box swallowed the 'Invoice Number' label"


def test_paragraph_tokens_ground_without_a_bounding_box():
    """docx / txt / xlsx carry no coordinates. Grounded is still True; the box is honestly None."""
    loc, grounded = Grounder(_paragraphs("Invoice Number: 042022")).check("042022")
    assert grounded is True
    assert loc["bounding_box"] is None
    assert loc["page_number"] == 1


def test_a_multi_page_value_reports_the_page_it_was_found_on():
    tokens = [_tok("nothing", page=1), _tok("042022", page=7)]
    loc, _ = Grounder(tokens).check("042022")
    assert loc["page_number"] == 7


# ───────────────────────── summary ─────────────────────────
def test_grounding_summary_separates_unknown_from_ungrounded():
    assert grounding_summary([True, True, False, None]) == {
        "checked": 3, "grounded": 2, "ungrounded": 1, "unknown": 1,
    }
    assert grounding_summary([]) == {"checked": 0, "grounded": 0, "ungrounded": 0, "unknown": 0}


# ───────────────────────── performance shape ─────────────────────────
def test_the_grounder_indexes_the_token_stream_once():
    """`_ocr_evidence` re-joined every token into one string per field — 40 passes for a 40-field config."""
    import inspect

    src = inspect.getsource(Grounder.__init__)
    assert "normalize(" in src, "tokens must be normalized once, at construction"
    assert "_norm" in src


# ───────────────────────── reasoning_trace comes from the document ─────────────────────────
def test_the_trace_quotes_the_document_not_the_model():
    """`reasoning_trace` used to be the model's own narration — or, on the compact path, nothing at all.

    A model asked to justify a value it invented invents a justification. Grounding already knows which
    tokens matched, so the trace is both free and true.
    """
    from agentcore.services.idp.grounding import evidence_trace

    g = Grounder(_words("Invoice Number 042022 Date 30 April 2022"))

    loc, grounded = g.check("042022")
    assert grounded is True
    assert evidence_trace(grounded, loc) == 'Found on page 1: "Invoice Number 042022 Date 30"'

    loc, grounded = g.check("PO-9999999")
    assert grounded is False
    assert evidence_trace(grounded, loc) == (
        "Not found in the document text — inferred, calculated, or reformatted by the model"
    )

    # No token stream -> we cannot say anything, so we say nothing.
    assert evidence_trace(None, None) is None


def test_the_snippet_is_the_surrounding_text_and_is_bounded():
    g = Grounder(_words("a " * 3 + "042022 " + "b " * 40))
    loc, _ = g.check("042022")
    assert "042022" in loc["snippet"]
    assert len(loc["snippet"]) <= 160


def test_a_reformatted_date_traces_back_to_the_prose_it_came_from():
    """The reviewer sees WHY 2022-04-30 is legitimate: the page says "30 April 2022"."""
    from agentcore.services.idp.grounding import evidence_trace

    loc, grounded = Grounder(_words("Invoice Date 30 April 2022 Terms Net30")).check("2022-04-30")
    assert grounded is True
    assert "30 April 2022" in evidence_trace(grounded, loc)


# ───────────────────────── the native engine must supply tokens ─────────────────────────
def test_the_document_upload_node_carries_the_native_token_stream():
    """Only PaddleOCR ever put `tokens` into the native payload, so a digital PDF reached the sink with
    none, and every field persisted `grounded=NULL` — unverifiable.

    `save_extraction_results` cannot ground what it is not given.
    """
    import inspect

    from agentcore.components.IDP import document_upload

    src = inspect.getsource(document_upload.IDPDocumentUpload.load)
    assert "tokens=tokens" in src, "the native entry node discards the token stream it just extracted"
