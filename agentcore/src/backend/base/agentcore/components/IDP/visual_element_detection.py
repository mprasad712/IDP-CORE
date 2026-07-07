from __future__ import annotations

from uuid import UUID

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, HandleInput, Output
from agentcore.schema.message import Message

_TOGGLE_TO_TYPE = {
    "detect_signatures": "signature",
    "detect_checkboxes": "checkbox",
    "detect_qr": "qr",
    "detect_stamps": "stamp",
    "detect_logos": "logo",
}


class IDPVisualElementDetection(Node):
    """Detect signatures / checkboxes / QR / barcodes / stamps / logos with a vision model and persist
    them (real work — wraps ``services/idp/visual_detection.detect_and_persist``)."""

    display_name = "Visual Element Detection"
    description = "Detects visual elements (signatures, checkboxes, QR/barcodes, stamps, logos) and persists them."
    icon = "ScanSearch"
    name = "IDPVisualElementDetection"

    inputs = [
        HandleInput(name="document", display_name="Document", input_types=["Message"], required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=False),
        BoolInput(name="detect_signatures", display_name="Detect Signatures", value=True),
        BoolInput(name="detect_checkboxes", display_name="Detect Checkboxes", value=True),
        BoolInput(name="detect_qr", display_name="Detect QR / Barcodes", value=True),
        BoolInput(name="detect_stamps", display_name="Detect Stamps", value=True),
        BoolInput(name="detect_logos", display_name="Detect Logos", value=True),
    ]
    outputs = [Output(display_name="Detected Elements", name="detected_elements", method="detect")]

    async def detect(self) -> Message:
        from loguru import logger

        from agentcore.services.deps import session_scope
        from agentcore.services.idp.graph_native.payload import carry, effective_payload, load_bytes
        from agentcore.services.idp.visual_detection import detect_and_persist

        payload = effective_payload(self, self.document)
        doc_id = payload.get("document_id")
        if not doc_id:
            self.status = "no document_id — detection skipped"
            return carry(self.document)

        enabled = {t for flag, t in _TOGGLE_TO_TYPE.items() if getattr(self, flag, False)}
        if "qr" in enabled:
            enabled.add("barcode")

        try:
            file_bytes = await load_bytes(payload)
            async with session_scope() as session:
                ids = await detect_and_persist(
                    session, UUID(str(doc_id)), file_bytes, payload.get("file_type", "pdf"),
                    enabled or None, llm_model=self.llm, clear_existing=True,
                )
            self.status = f"{len(ids)} element(s) detected"
            return carry(self.document, detected=len(ids))
        except Exception as exc:  # detection is best-effort — never fail the document
            logger.warning(f"[IDP native] visual detection skipped: {exc}")
            self.status = f"detection skipped ({exc})"
            return carry(self.document)
