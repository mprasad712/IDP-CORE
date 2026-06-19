"""Public API for IDP document/report serialization.

``serialize_document(payload, fmt)`` turns a single ``build_doc_payload`` dict into
downloadable bytes; ``serialize_table(columns, rows, fmt)`` does the same for generic
tabular report rows. Both return ``(bytes, media_type, file_extension)`` and raise
``ValueError`` for an unsupported format.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from agentcore.services.idp.output.schema import build_doc_payload
from agentcore.services.idp.output.serializers import (
    tabular_to_csv,
    tabular_to_xlsx,
    tabular_to_xml,
    to_csv,
    to_json,
    to_txt,
    to_xlsx,
    to_xml,
)

__all__ = [
    "build_doc_payload",
    "serialize_document",
    "serialize_table",
    "SUPPORTED_FORMATS",
    "SUPPORTED_TABULAR_FORMATS",
]

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Per-document export formats (the per-PO download offers all five).
SUPPORTED_FORMATS = {"csv", "excel", "xml", "json", "txt"}
# Tabular (report) export formats — a subset; per-row JSON/TXT don't suit a flat bulk table.
SUPPORTED_TABULAR_FORMATS = {"csv", "excel", "xml"}

_EXT = {"csv": "csv", "excel": "xlsx", "xml": "xml", "json": "json", "txt": "txt"}

_DOC_SERIALIZERS = {
    "csv": (to_csv, "text/csv"),
    "excel": (to_xlsx, _XLSX_MEDIA),
    "xml": (to_xml, "application/xml"),
    "json": (to_json, "application/json"),
    "txt": (to_txt, "text/plain; charset=utf-8"),
}

_TABULAR_SERIALIZERS = {
    "csv": (tabular_to_csv, "text/csv"),
    "excel": (tabular_to_xlsx, _XLSX_MEDIA),
    "xml": (tabular_to_xml, "application/xml"),
}


def serialize_document(payload: dict, fmt: str) -> tuple[bytes, str, str]:
    """Serialize one document payload → ``(bytes, media_type, file_extension)``.

    Raises ``ValueError`` if ``fmt`` is not in :data:`SUPPORTED_FORMATS`.
    """
    key = (fmt or "").lower()
    if key not in _DOC_SERIALIZERS:
        raise ValueError(f"Unsupported export format: {fmt!r}. Supported: {sorted(SUPPORTED_FORMATS)}")
    fn, media = _DOC_SERIALIZERS[key]
    return fn(payload), media, _EXT[key]


def serialize_table(
    columns: Sequence[str],
    rows: Iterable[dict],
    fmt: str,
    *,
    sheet_name: str = "Data",
    root_tag: str = "report",
    row_tag: str = "record",
) -> tuple[bytes, str, str]:
    """Serialize generic tabular rows → ``(bytes, media_type, file_extension)``.

    Raises ``ValueError`` if ``fmt`` is not in :data:`SUPPORTED_TABULAR_FORMATS`.
    """
    key = (fmt or "").lower()
    if key not in _TABULAR_SERIALIZERS:
        raise ValueError(
            f"Unsupported tabular format: {fmt!r}. Supported: {sorted(SUPPORTED_TABULAR_FORMATS)}"
        )
    fn, media = _TABULAR_SERIALIZERS[key]
    if key == "excel":
        data = fn(columns, rows, sheet_name=sheet_name)
    elif key == "xml":
        data = fn(columns, rows, root_tag=root_tag, row_tag=row_tag)
    else:
        data = fn(columns, rows)
    return data, media, _EXT[key]
