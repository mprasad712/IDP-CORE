from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, MessageTextInput, Output


class IDPMultiBranchRouter(Node):
    display_name = "Multi-Branch Router"
    description = "Routes to up to 5 named output paths based on the value of a classification or type field."
    icon = "Network"
    name = "IDPMultiBranchRouter"

    inputs = [
        HandleInput(
            name="document",
            display_name="Input",
            input_types=["Message", "Data"],
            required=True,
        ),
        MessageTextInput(name="route_field", display_name="Route Field", value="document_type",
                         info="Field whose value determines which output to take."),
        MessageTextInput(name="route_1", display_name="Route 1 Value", value="invoice"),
        MessageTextInput(name="route_2", display_name="Route 2 Value", value="receipt"),
        MessageTextInput(name="route_3", display_name="Route 3 Value", value="contract", advanced=True),
        MessageTextInput(name="route_4", display_name="Route 4 Value", value="", advanced=True),
        MessageTextInput(name="route_5", display_name="Route 5 Value", value="", advanced=True),
    ]

    outputs = [
        Output(display_name="Route 1", name="route_1_out", method="route_1_out", group_outputs=True),
        Output(display_name="Route 2", name="route_2_out", method="route_2_out", group_outputs=True),
        Output(display_name="Route 3", name="route_3_out", method="route_3_out", group_outputs=True),
        Output(display_name="Route 4", name="route_4_out", method="route_4_out", group_outputs=True),
        Output(display_name="Route 5", name="route_5_out", method="route_5_out", group_outputs=True),
        Output(display_name="Unmatched", name="unmatched", method="unmatched_out", group_outputs=True),
    ]

    def _get_field_value(self) -> str:
        # Resolve the route field (e.g. document_type) from the IDP payload + shared channel — the
        # classifier writes the type there, not as a Message attribute. route_1..5 match against this.
        from agentcore.services.idp.graph_native.payload import resolve_field

        val = resolve_field(self.document, (self.route_field or "").strip(), component=self)
        return str(val if val is not None else "")

    def _matched_route(self) -> int:
        """Returns 1-5 for a matched route, 0 for unmatched."""
        fv = self._get_field_value()
        for i, attr in enumerate(["route_1", "route_2", "route_3", "route_4", "route_5"], start=1):
            rv = (getattr(self, attr) or "").strip()
            if rv and fv == rv:
                return i
        return 0

    def _route_out(self, route_num: int):
        matched = self._matched_route()
        self.status = f"Field='{self._get_field_value()}' → Route {matched if matched else 'unmatched'}"
        if matched == route_num:
            return self.document
        self.stop(f"route_{route_num}_out")
        return self.document

    def route_1_out(self): return self._route_out(1)
    def route_2_out(self): return self._route_out(2)
    def route_3_out(self): return self._route_out(3)
    def route_4_out(self): return self._route_out(4)
    def route_5_out(self): return self._route_out(5)

    def unmatched_out(self):
        matched = self._matched_route()
        if matched == 0:
            return self.document
        self.stop("unmatched")
        return self.document
