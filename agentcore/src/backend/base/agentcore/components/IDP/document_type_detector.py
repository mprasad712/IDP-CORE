from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, IntInput, Output
from agentcore.schema.data import Data
from agentcore.schema.message import Message


class IDPDocumentTypeDetector(Node):
    display_name = "Document Type Detector"
    description = "Detects whether a document has a native text layer (digital) or is image-only (scanned)."
    icon = "FileScan"
    name = "IDPDocumentTypeDetector"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
        IntInput(
            name="min_text_length",
            display_name="Min Native Text Length",
            value=50,
            advanced=True,
            info="Minimum character count to classify a page as digital.",
        ),
    ]

    outputs = [
        Output(display_name="Document + Type Flag", name="document_with_type", method="document_with_type"),
        Output(display_name="Type Flag (digital / scanned)", name="document_type_flag", method="document_type_flag"),
    ]

    def _detect_type(self) -> str:
        src = self.document
        text = ""
        if isinstance(src, Message):
            text = src.text or ""
        elif isinstance(src, Data):
            text = src.text or ""
        elif isinstance(src, str):
            text = src
        else:
            text = str(src)
        return "digital" if len(text.strip()) >= self.min_text_length else "scanned"

    def document_with_type(self) -> Message:
        doc_type = self._detect_type()
        self.status = f"Detected: {doc_type}"
        src = self.document
        if isinstance(src, Message):
            if src.data is None:
                src.data = {}
            src.data["document_type"] = doc_type
            return src
        msg = Message(text=str(src), data={"document_type": doc_type})
        return msg

    def document_type_flag(self) -> Data:
        doc_type = self._detect_type()
        return Data(data={"document_type": doc_type}, text=doc_type)
