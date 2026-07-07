from __future__ import annotations

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
        src = self.document
        text = ""
        if isinstance(src, Message):
            text = src.text or ""
        else:
            text = str(src)
        return len(text.strip()) >= self.min_text_length

    def _tagged(self, label: str) -> Message:
        # Carry the detected kind in the IDP payload (overall_kind) — NEVER in .data, which the engine
        # strips (losing document_id mid-graph). Downstream reads it via resolve_field(src, "document_type").
        from agentcore.services.idp.graph_native.payload import carry

        return carry(self.document, overall_kind=label)

    # ── outputs ───────────────────────────────────────────────────────────────

    def digital_path(self) -> Message:
        if not self._extension_allowed():
            self.status = f"Skipped — extension not allowed: .{self._get_extension()}"
            self.stop("digital_path")
            return self.document

        digital = self._is_digital()
        self.status = f"{'digital' if digital else 'scanned'}"

        if digital:
            return self._tagged(self.digital_label or "digital")
        self.stop("digital_path")
        return self.document

    def scanned_path(self) -> Message:
        if not self._extension_allowed():
            self.stop("scanned_path")
            return self.document

        digital = self._is_digital()
        if not digital:
            return self._tagged(self.scanned_label or "scanned")
        self.stop("scanned_path")
        return self.document
