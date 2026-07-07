from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, MessageTextInput, Output
from agentcore.schema.message import Message


class IDPPaddleOCR(Node):
    """OCR a document with PaddleOCR and put the recognized text into the flow (real work)."""

    display_name = "PaddleOCR"
    description = "Runs PaddleOCR on the document and adds the recognized text to the flow."
    icon = "ScanText"
    name = "IDPPaddleOCR"

    inputs = [
        HandleInput(name="document", display_name="Document", input_types=["Message"], required=True),
        MessageTextInput(name="language", display_name="Language", value="en", info="PaddleOCR language code."),
    ]
    outputs = [Output(display_name="Text Blocks", name="text_blocks", method="ocr")]

    async def ocr(self) -> Message:
        from agentcore.services.idp.graph_native.payload import carry, effective_payload, load_bytes
        from agentcore.services.idp.ocr import run_paddle_ocr
        from agentcore.services.idp.restruct import build_merged_text

        payload = effective_payload(self, self.document)
        file_bytes = await load_bytes(payload)
        tokens = await run_paddle_ocr(file_bytes, payload.get("file_type", "pdf"), (self.language or "en"))
        text = build_merged_text(tokens)
        self.status = f"{len(tokens)} token(s), {len(text)} chars"
        return carry(self.document, text=text, tokens=tokens)
