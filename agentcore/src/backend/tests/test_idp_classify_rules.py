"""Sanity tests for the model-free keyword classifier fallback (classify_via_rules).

Used by the Document Classifier node when NO Language Model is connected — so the classifier
still works offline. Pure-function tests; no DB / no model.
"""

from agentcore.services.idp.classification import classify_via_rules

INVOICE_TEXT = (
    "ACME SUPPLIES LTD TAX INVOICE Invoice Number: INV-2026-00789 Invoice Date: 2026-07-01 "
    "Bill To: Globex Corporation Subtotal 430.00 Tax 77.40 Total Amount Due 507.40 USD"
)
MEDICAL_TEXT = (
    "CITY GENERAL HOSPITAL DISCHARGE SUMMARY / MEDICAL REPORT Patient Name: John A. Patient "
    "Date of Admission 2026-06-20 Diagnosis: acute appendicitis Attending Physician Dr. Chen"
)
AADHAAR_TEXT = (
    "GOVERNMENT OF INDIA Unique Identification Authority of India AADHAAR Name: Rahul Sharma "
    "DOB 15/08/1990 Aadhaar No 1234 5678 9012"
)
CANDIDATES = ["Invoice", "Medical Report"]


def test_rules_matches_invoice():
    r = classify_via_rules(CANDIDATES, INVOICE_TEXT)
    assert r.predicted_type == "Invoice"
    assert r.confidence >= 0.75  # a full name-token match clears a typical confidence gate


def test_rules_matches_medical_report():
    r = classify_via_rules(CANDIDATES, MEDICAL_TEXT)
    assert r.predicted_type == "Medical Report"
    assert r.confidence >= 0.75


def test_rules_unknown_for_unrelated_type():
    # An Aadhaar card is neither Invoice nor Medical Report -> must NOT be force-matched.
    r = classify_via_rules(CANDIDATES, AADHAAR_TEXT)
    assert r.predicted_type == "unknown"


def test_rules_empty_text_is_unknown():
    r = classify_via_rules(CANDIDATES, "")
    assert r.predicted_type == "unknown"
    assert r.confidence == 0.0


def test_rules_no_candidates_is_unknown():
    r = classify_via_rules([], INVOICE_TEXT)
    assert r.predicted_type == "unknown"


def test_rules_confidence_capped():
    # Heuristic confidence must never exceed the 0.9 cap (it's not a model-grade score).
    r = classify_via_rules(CANDIDATES, INVOICE_TEXT)
    assert r.confidence <= 0.9


def test_rules_partial_name_match_stays_below_default_gate():
    # Codex [3]: a doc that hits only HALF a multi-token name (+ description overlap) must NOT reach a
    # model-grade confidence — it stays under the classifier's default 0.75 gate so it falls to 'unknown'
    # (skip) rather than mis-routing to the wrong Field Configuration.
    text = "quarterly report total amount summary vendor"  # has 'report', NOT 'medical'
    r = classify_via_rules(["Medical Report"], text, descriptions={"Medical Report": "total amount vendor summary"})
    assert r.confidence < 0.75
