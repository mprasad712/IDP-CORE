from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, DropdownInput, HandleInput, MessageTextInput, Output


class IDPConditionNode(Node):
    display_name = "Condition"
    description = "If/else branch: routes the document down the true or false path based on a configurable condition."
    icon = "GitBranch"
    name = "IDPConditionNode"

    inputs = [
        HandleInput(
            name="document",
            display_name="Input",
            input_types=["Message", "Data"],
            info="Document or data to evaluate the condition against.",
            required=True,
        ),
        MessageTextInput(
            name="field",
            display_name="Field / Flag",
            info="Name of the field or flag to test (e.g. document_type, confidence). Leave blank to test the full input.",
            value="",
        ),
        DropdownInput(
            name="operator",
            display_name="Operator",
            options=[
                "equals", "not_equals", "contains",
                "greater_than", "less_than",
                "greater_than_or_equal", "less_than_or_equal",
                "is_empty", "is_not_empty",
            ],
            value="equals",
        ),
        MessageTextInput(
            name="value",
            display_name="Threshold Value",
            info="Value to compare against. For numeric operators enter a number (e.g. 0.8).",
            value="",
        ),
    ]

    outputs = [
        Output(display_name="True", name="true_path", method="true_path", group_outputs=True),
        Output(display_name="False", name="false_path", method="false_path", group_outputs=True),
    ]

    def _resolve_field_value(self) -> str:
        from agentcore.services.idp.graph_native.payload import resolve_field

        src = self.document
        field = (self.field or "").strip()

        if not field:
            return str(src) if src is not None else ""

        # Resolve from the IDP payload (extracted fields, overall_kind, predicted_type, decision, …)
        # incl. the shared channel — instead of getattr(Message, field), which is blind to the working-set.
        val = resolve_field(src, field, component=self)
        if val is None:
            val = getattr(src, "text", "") if not isinstance(src, (dict, str)) else src
        return str(val if val is not None else "")

    def _evaluate(self) -> bool:
        lhs = self._resolve_field_value()
        rhs = (self.value or "").strip()
        op = self.operator

        if op == "is_empty":
            return not lhs.strip()
        if op == "is_not_empty":
            return bool(lhs.strip())
        if op == "equals":
            return lhs == rhs
        if op == "not_equals":
            return lhs != rhs
        if op == "contains":
            return rhs in lhs
        try:
            lhs_n, rhs_n = float(lhs), float(rhs)
            if op == "greater_than":
                return lhs_n > rhs_n
            if op == "less_than":
                return lhs_n < rhs_n
            if op == "greater_than_or_equal":
                return lhs_n >= rhs_n
            if op == "less_than_or_equal":
                return lhs_n <= rhs_n
        except ValueError:
            pass
        return False

    def true_path(self):
        result = self._evaluate()
        self.status = f"Condition: {result}"
        if result:
            return self.document
        self.stop("true_path")
        return self.document

    def false_path(self):
        result = self._evaluate()
        if not result:
            return self.document
        self.stop("false_path")
        return self.document
