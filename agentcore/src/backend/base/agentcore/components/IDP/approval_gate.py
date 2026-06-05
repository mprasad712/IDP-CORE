from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, MessageTextInput, Output
from agentcore.schema.data import Data


class IDPApprovalGate(Node):
    display_name = "Approval Gate"
    description = "Reads the rule outcome and routes to auto-approved or pending-review for the Processed Docs page."
    icon = "ShieldCheck"
    name = "IDPApprovalGate"

    inputs = [
        DataInput(
            name="data",
            display_name="Rules Result",
            info="Output from Rules / Conditions or Confidence Router.",
            required=True,
        ),
        MessageTextInput(
            name="approval_field",
            display_name="Approval Field",
            value="rule_action",
            info="Field name in the data that contains the approval decision.",
        ),
        MessageTextInput(
            name="approve_value",
            display_name="Auto-Approve Value",
            value="auto_approve",
            info="Field value that triggers the auto-approved path; anything else goes to pending review.",
        ),
    ]

    outputs = [
        Output(display_name="Auto Approved", name="auto_approved", method="auto_approved", group_outputs=True),
        Output(display_name="Pending Review", name="pending_review", method="pending_review", group_outputs=True),
    ]

    def _is_approved(self) -> bool:
        field = (self.approval_field or "rule_action").strip()
        expected = (self.approve_value or "auto_approve").strip()
        src = self.data
        if isinstance(src, Data):
            actual = str(src.data.get(field, ""))
        elif isinstance(src, dict):
            actual = str(src.get(field, ""))
        else:
            actual = str(getattr(src, field, ""))
        return actual == expected

    def auto_approved(self) -> Data:
        approved = self._is_approved()
        self.status = "auto_approved" if approved else "pending_review"
        if approved:
            if isinstance(self.data, Data):
                self.data.data["_approval_status"] = "auto_approved"
            return self.data
        self.stop("auto_approved")
        return self.data

    def pending_review(self) -> Data:
        approved = self._is_approved()
        if not approved:
            if isinstance(self.data, Data):
                self.data.data["_approval_status"] = "pending_review"
            return self.data
        self.stop("pending_review")
        return self.data
