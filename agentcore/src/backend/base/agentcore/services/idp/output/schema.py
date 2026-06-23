"""Build a serialization-ready payload from a processed IDP document + its extracted fields.

Pure transform — no DB, no FastAPI. Takes already-loaded ORM rows and produces a plain dict
of primitives that the format serializers (CSV/Excel/XML/JSON/TXT) turn into bytes.

Audit honesty (the export must not imply a human reviewed something they didn't): three
distinct value columns —

  predicted_value = ``extracted_value`` (what the LLM produced; may be ``""`` or ``None``)
  audited_value   = ``reviewed_value`` ONLY when ``is_reviewed`` is True, else ``None``
  is_reviewed     = the human-review flag

An intentionally-blank human correction (``""``) is preserved distinct from "never reviewed"
(``None``). ``values="final"`` collapses the two columns into a single ``value`` =
``reviewed_value if is_reviewed else extracted_value`` (the audited-or-predicted truth).
"""
from __future__ import annotations

from typing import Any

# Column orders the format serializers iterate over, so both value-modes share one code path.
_HEADER_COLS_BOTH = ["field_name", "predicted_value", "audited_value", "is_reviewed", "confidence"]
_HEADER_COLS_FINAL = ["field_name", "value", "confidence"]
_LINE_COLS_BOTH = ["row_index", "column_name", "predicted_value", "audited_value", "is_reviewed", "confidence"]
_LINE_COLS_FINAL = ["row_index", "column_name", "value", "confidence"]


def _pct(conf: Any) -> float | None:
    """Confidence is stored 0–1; surface it as a 0–100 percentage (1 dp) like the UI badges."""
    return None if conf is None else round(float(conf) * 100.0, 1)


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def build_doc_payload(doc, headers, line_items, review_session=None, *, values: str = "both") -> dict:
    """Assemble the export payload for one document. ``values`` ∈ {"both", "final"}.

    ``doc`` is an ``IdpDocument``; ``headers``/``line_items`` are the extracted-field rows
    (``IdpExtractedHeader``/``IdpExtractedLineItem``); ``review_session`` is the latest
    ``IdpReviewSession`` or ``None``. Returns a plain dict (no ORM objects) ready to serialize.
    """
    if values not in ("both", "final"):
        raise ValueError(f"values must be 'both' or 'final', got {values!r}")

    final = values == "final"
    header_cols = _HEADER_COLS_FINAL if final else _HEADER_COLS_BOTH
    line_cols = _LINE_COLS_FINAL if final else _LINE_COLS_BOTH

    def _field_row(f, *, is_line: bool) -> dict:
        conf = _pct(f.confidence_score)
        if final:
            row = {"value": f.reviewed_value if f.is_reviewed else f.extracted_value, "confidence": conf}
        else:
            row = {
                "predicted_value": f.extracted_value,
                # audited_value is the human value ONLY when actually reviewed; else None (not "").
                "audited_value": f.reviewed_value if f.is_reviewed else None,
                "is_reviewed": bool(f.is_reviewed),
                "confidence": conf,
            }
        base = (
            {"row_index": f.row_index, "column_name": f.column_name}
            if is_line
            else {"field_name": f.field_name}
        )
        return {**base, **row}

    reviewer_id = getattr(review_session, "reviewed_by", None) if review_session else None
    document = {
        "id": str(doc.id),
        "filename": doc.original_filename,
        "predicted_type": doc.predicted_type,
        "status": doc.status,
        "overall_confidence": _pct(doc.overall_confidence),
        "uploaded_at": _iso(doc.created_at),
        "pipeline_completed_at": _iso(doc.processing_completed_at),
        "reviewed_at": _iso(getattr(review_session, "review_completed_at", None)) if review_session else None,
        "reviewer": str(reviewer_id) if reviewer_id else None,
    }

    return {
        "values_mode": values,
        "document": document,
        "header_columns": header_cols,
        "headers": [_field_row(h, is_line=False) for h in headers],
        "line_item_columns": line_cols,
        "line_items": [_field_row(li, is_line=True) for li in line_items],
    }
