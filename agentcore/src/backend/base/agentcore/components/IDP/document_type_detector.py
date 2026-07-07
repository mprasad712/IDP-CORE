from __future__ import annotations

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, HandleInput, IntInput, MessageTextInput, Output
from agentcore.schema.message import Message


class IDPDocumentTypeDetector(Node):
    display_name = "Document Type Detector"
    description = (
        "Filters documents by extension, then detects whether the document is digital "
        "or scanned and routes to the matching output path."
    )
    icon = "FileScan"
    name = "IDPDocumentTypeDetector"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
        MessageTextInput(
            name="allowed_extensions",
            display_name="Allowed Extensions",
            value="pdf,png,jpg,jpeg,tiff,bmp,xlsx,xls,docx,doc",
            info="Comma-separated list of file extensions to accept. Documents with other extensions are skipped.",
        ),
        BoolInput(
            name="skip_unmatched",
            display_name="Skip Unmatched Extensions",
            value=True,
            info="When enabled, documents whose extension is not in the allowed list are silently dropped.",
        ),
        MessageTextInput(
            name="digital_label",
            display_name="Digital Label",
            value="digital",
            advanced=True,
            info="Value attached to documents that have a native text layer.",
        ),
        MessageTextInput(
            name="scanned_label",
            display_name="Scanned Label",
            value="scanned",
            advanced=True,
            info="Value attached to documents that are image-only (no native text layer).",
        ),
        IntInput(
            name="min_text_length",
            display_name="Min Native Text Length",
            value=50,
            advanced=True,
            info="Minimum character count to classify a document as digital.",
        ),
    ]

    outputs = [
        Output(display_name="Digital (True)", name="digital_path", method="digital_path", group_outputs=True),
        Output(display_name="Scanned (False)", name="scanned_path", method="scanned_path", group_outputs=True),
    ]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get_extension(self) -> str:
        # The file type rides in the IDP payload (file_type / file_name) — NOT in .data (which the
        # engine strips). Read it there first; fall back to legacy .data/text only for non-IDP inputs.
        from agentcore.services.idp.graph_native.payload import get_payload

        payload = get_payload(self.document)
        ft = str(payload.get("file_type") or "").lower().lstrip(".")
        if ft:
            return ft
        name = str(payload.get("file_name") or "")
        if "." in name:
            return name.rsplit(".", 1)[-1].lower().strip()
        src = self.document
        if isinstance(src, Message):
            data = src.data or {}
            cand = str(data.get("source_file", data.get("file_path", src.text or "")))
            if "." in cand:
                return cand.rsplit(".", 1)[-1].lower().strip()
        return ""

    def _extension_allowed(self) -> bool:
        ext = self._get_extension()
        if not ext:
            return not self.skip_unmatched
        allowed = {e.strip().lower().lstrip(".") for e in (self.allowed_extensions or "").split(",") if e.strip()}
        return ext in allowed

    def _is_digital(self) -> bool:
        # Fallback only (used when the raw bytes can't be loaded to classify per page).
        src = self.document
        text = src.text if isinstance(src, Message) else str(src)
        return len((text or "").strip()) >= self.min_text_length

    async def _overall_kind(self) -> str:
        """Classify the document as 'digital' / 'scanned' / 'mixed' by PER-PAGE text coverage rather than
        the whole-doc text length. A mixed PDF — some digital pages, some image-only — is treated as
        scanned so it takes the OCR path and its image-only pages aren't silently dropped. (Codex #6)"""
        cached = getattr(self, "_kind_cache", None)
        if cached is not None:
            return cached

        from agentcore.services.idp import text_layer
        from agentcore.services.idp.graph_native.payload import effective_payload, load_bytes

        kind = "digital" if self._is_digital() else "scanned"   # heuristic fallback
        page_status: dict = {}
        try:
            payload = effective_payload(self, self.document)
            file_type = str(payload.get("file_type") or self._get_extension() or "pdf").lower().lstrip(".")
            file_bytes = await load_bytes(payload)
            if file_bytes:
                kind, page_status = text_layer.classify_document(
                    file_bytes, file_type, min_text_length=int(self.min_text_length or 50)
                )
        except Exception as exc:  # noqa: BLE001 — never fail routing on a classify hiccup
            logger.debug(f"[DocumentTypeDetector] per-page classify failed, using text heuristic: {exc}")

        self._kind_cache = kind
        self._page_status_cache = page_status
        return kind

    def _tagged(self, label: str) -> Message:
        # Carry the detected kind + per-page status in the IDP payload (overall_kind / page_status) —
        # NEVER in .data, which the engine strips. Downstream reads overall_kind via
        # resolve_field(src, "document_type"); an OCR node can OCR only the scanned pages.
        from agentcore.services.idp.graph_native.payload import carry

        page_status = getattr(self, "_page_status_cache", None) or {}
        return carry(self.document, overall_kind=label, page_status=page_status or None)

    # ── outputs ───────────────────────────────────────────────────────────────

    async def digital_path(self) -> Message:
        if not self._extension_allowed():
            self.status = f"Skipped — extension not allowed: .{self._get_extension()}"
            self.stop("digital_path")
            return self.document

        kind = await self._overall_kind()
        self.status = kind
        # Only a fully-digital document takes the digital path; 'scanned' and 'mixed' go to OCR.
        if kind == "digital":
            return self._tagged(self.digital_label or "digital")
        self.stop("digital_path")
        return self.document

    async def scanned_path(self) -> Message:
        if not self._extension_allowed():
            self.stop("scanned_path")
            return self.document

        kind = await self._overall_kind()
        if kind != "digital":   # 'scanned' OR 'mixed' → OCR path (so image-only pages get read)
            self.status = kind
            return self._tagged(self.scanned_label or "scanned")
        self.stop("scanned_path")
        return self.document
