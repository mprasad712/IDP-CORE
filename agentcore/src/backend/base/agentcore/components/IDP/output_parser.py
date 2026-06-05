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
        result = self.branch_a if self.branch_a is not None else self.branch_b
        self.status = "Merged branch A" if self.branch_a is not None else "Merged branch B"
        return result
