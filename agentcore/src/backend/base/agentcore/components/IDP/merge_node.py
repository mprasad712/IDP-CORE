from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, Output
from agentcore.schema.data import Data
from agentcore.schema.message import Message


class IDPMergeNode(Node):
    display_name = "Merge"
    description = "Joins two branches back into a single flow after a Condition split."
    icon = "Merge"
    name = "IDPMergeNode"

    inputs = [
        HandleInput(
            name="branch_a",
            display_name="Branch A",
            input_types=["Message", "Data"],
            info="First branch input (e.g. true path).",
            required=False,
        ),
        HandleInput(
            name="branch_b",
            display_name="Branch B",
            input_types=["Message", "Data"],
            info="Second branch input (e.g. false path).",
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
