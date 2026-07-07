from __future__ import annotations

import json

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, DropdownInput, MessageTextInput, Output


class IDPRulesConditions(Node):
    """Customisable rule builder: evaluate multiple condition rows against the extracted fields and
    route the document to Passed or Failed.

    Rows are combined with AND / OR. Each row is ``{"field": ..., "op": ..., "value": ...}`` and its
    field is resolved from the IDP working-set (extracted values, overall_kind, predicted_type, …) via
    the shared channel — not a blind Message attribute. The outcome is recorded in the payload as
    ``rule_action`` (auto_approve / review) so a downstream Approval Gate can act on it.
    """

    display_name = "Rules / Conditions"
    description = "Evaluates configurable rules against the extracted data and routes passed / failed."
    icon = "GitBranch"
    name = "IDPRulesConditions"

    inputs = [
        DataInput(
            name="data",
            display_name="Extracted Data",
            info="Extracted data to evaluate rules against.",
            required=True,
        ),
        DropdownInput(
            name="logic_operator",
            display_name="Logic Operator",
            options=["AND", "OR"],
            value="AND",
            info="Combine all condition rows with AND or OR logic.",
        ),
        MessageTextInput(
            name="conditions",
            display_name="Conditions (JSON)",
            value="[]",
            info='Array of condition objects, e.g. [{"field":"total","op":"gt","value":0}]',
        ),
    ]

    outputs = [
        Output(display_name="Passed", name="passed", method="passed", group_outputs=True),
        Output(display_name="Failed", name="failed", method="failed", group_outputs=True),
    ]

    # Accept both short (gt/lt) and long (greater_than) operator spellings.
    _OP_ALIASES = {
        "eq": "equals", "==": "equals", "equals": "equals",
        "ne": "not_equals", "!=": "not_equals", "not_equals": "not_equals",
        "gt": "greater_than", ">": "greater_than", "greater_than": "greater_than",
        "lt": "less_than", "<": "less_than", "less_than": "less_than",
        "gte": "greater_than_or_equal", ">=": "greater_than_or_equal",
        "greater_than_or_equal": "greater_than_or_equal",
        "lte": "less_than_or_equal", "<=": "less_than_or_equal",
        "less_than_or_equal": "less_than_or_equal",
        "contains": "contains",
        "is_empty": "is_empty", "is_not_empty": "is_not_empty",
    }

    # ── evaluation ─────────────────────────────────────────────────────────────
    def _parse_conditions(self) -> list[dict]:
        raw = self.conditions
        if isinstance(raw, list):
            return [c for c in raw if isinstance(c, dict)]
        try:
            parsed = json.loads(str(raw or "[]"))
        except (TypeError, ValueError):
            return []
        return [c for c in parsed if isinstance(c, dict)] if isinstance(parsed, list) else []

    def _eval_one(self, cond: dict) -> bool:
        from agentcore.services.idp.graph_native.payload import resolve_field

        field = str(cond.get("field", "")).strip()
        op = self._OP_ALIASES.get(str(cond.get("op", "equals")).strip().lower(), "equals")
        expected = cond.get("value", "")
        actual = resolve_field(self.data, field, component=self) if field else getattr(self.data, "text", "")
        actual_s = "" if actual is None else str(actual)
        expected_s = "" if expected is None else str(expected)

        if op == "is_empty":
            return not actual_s.strip()
        if op == "is_not_empty":
            return bool(actual_s.strip())
        if op == "equals":
            return actual_s == expected_s
        if op == "not_equals":
            return actual_s != expected_s
        if op == "contains":
            return expected_s in actual_s
        try:
            a, b = float(actual_s), float(expected_s)
        except (TypeError, ValueError):
            return False
        if op == "greater_than":
            return a > b
        if op == "less_than":
            return a < b
        if op == "greater_than_or_equal":
            return a >= b
        if op == "less_than_or_equal":
            return a <= b
        return False

    def _passes(self) -> bool:
        conds = self._parse_conditions()
        if not conds:
            return True  # no rules configured → pass (don't silently block the document)
        results = [self._eval_one(c) for c in conds]
        if str(self.logic_operator or "AND").upper() == "OR":
            return any(results)
        return all(results)

    # ── outputs ────────────────────────────────────────────────────────────────
    def _result(self, want_pass: bool):
        from agentcore.services.idp.graph_native.payload import carry

        passed = self._passes()
        self.status = f"{'PASSED' if passed else 'FAILED'} ({self.logic_operator})"
        if passed == want_pass:
            # Record the outcome in the payload (rule_action) so a downstream Approval Gate can
            # auto-approve / route to review. Never write .data — the engine strips it.
            return carry(self.data, rule_action="auto_approve" if passed else "review")
        self.stop("passed" if want_pass else "failed")
        return self.data

    def passed(self):
        return self._result(True)

    def failed(self):
        return self._result(False)
