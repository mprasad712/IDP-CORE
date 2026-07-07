from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, Output
from agentcore.schema.data import Data
from agentcore.schema.message import Message


class IDPOutputParser(Node):
    display_name = "Output Parser"
    description = "Joins two branches (digital and scanned paths) back into a single flow."
    icon = "Merge"
    name = "IDPOutputParser"

    inputs = [
        HandleInput(
            name="branch_a",
            display_name="Branch A",
            input_types=["Message", "Data"],
            info="First branch input (e.g. digital path or OCR output).",
            required=False,
        ),
        HandleInput(
            name="branch_b",
            display_name="Branch B",
            input_types=["Message", "Data"],
            info="Second branch input (e.g. scanned / OCR path).",
            required=False,
        ),
    ]

    outputs = [
        Output(display_name="Merged", name="merged", method="merge"),
    ]

    def merge(self):
        a, b = self.branch_a, self.branch_b

        def _has_text(m) -> bool:
            t = getattr(m, "text", None)
            return bool(t and str(t).strip())

        # A router (e.g. Document Type Detector) activates exactly ONE branch and STOPS the other — but
        # the stopped branch can still forward the pre-routing message, which on the scanned path has NO
        # OCR text yet. Blindly preferring branch A then drops the OCR/native text (the classifier +
        # chunker see nothing). So prefer the branch that actually carries text — the branch that truly
        # ran — and only fall back to presence order when neither has text.
        if a is not None and _has_text(a):
            result, which = a, "A"
        elif b is not None and _has_text(b):
            result, which = b, "B"
        elif a is not None:
            result, which = a, "A"
        else:
            result, which = b, "B"
        self.status = f"Merged branch {which}"
        return result
