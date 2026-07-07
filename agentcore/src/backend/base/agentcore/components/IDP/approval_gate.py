from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, MessageTextInput, Output


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
        from agentcore.services.idp.graph_native.payload import resolve_field

        field = (self.approval_field or "rule_action").strip()
        expected = (self.approve_value or "auto_approve").strip()
        # Resolve the rule outcome from the IDP payload + shared channel (Rules/Confidence Router write
        # it into the working-set), not getattr(Message, field) which is blind to the working-set.
        actual = resolve_field(self.data, field, component=self)
        return str(actual if actual is not None else "") == expected

    def auto_approved(self):
        from agentcore.services.idp.graph_native.payload import carry

        approved = self._is_approved()
        self.status = "auto_approved" if approved else "pending_review"
        if approved:
            # Record the decision in the payload (never .data — the engine strips it) so the sink can
            # finalize the document as auto_approved.
            return carry(self.data, decision="auto_approved")
        self.stop("auto_approved")
        return self.data

    def pending_review(self):
        from agentcore.services.idp.graph_native.payload import carry

        approved = self._is_approved()
        if not approved:
            return carry(self.data, decision="pending_review")
        self.stop("pending_review")
        return self.data
