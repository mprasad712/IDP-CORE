from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, FloatInput, MessageTextInput, Output
from agentcore.schema.data import Data


class IDPConfidenceRouter(Node):
    display_name = "Confidence Router"
    description = "Routes extracted data based on overall confidence score — high confidence to approval, low to review."
    icon = "Gauge"
    name = "IDPConfidenceRouter"

    inputs = [
        DataInput(
            name="data",
            display_name="Extracted Data",
            info="Extracted data containing a confidence score field.",
            required=True,
        ),
        MessageTextInput(
            name="confidence_field",
            display_name="Confidence Field",
            value="confidence",
            info="Name of the field that holds the overall confidence score (0–1).",
        ),
        FloatInput(
            name="threshold",
            display_name="Confidence Threshold",
            value=0.8,
            info="Scores at or above this value go to High Confidence; below go to Low Confidence.",
        ),
    ]

    outputs = [
        Output(display_name="High Confidence", name="high_confidence", method="high_confidence", group_outputs=True),
        Output(display_name="Low Confidence", name="low_confidence", method="low_confidence", group_outputs=True),
    ]

    def _get_score(self) -> float:
        field = (self.confidence_field or "confidence").strip()
        src = self.data
        raw = None
        if isinstance(src, Data):
            raw = src.data.get(field)
        elif isinstance(src, dict):
            raw = src.get(field)
        else:
            raw = getattr(src, field, None)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def high_confidence(self) -> Data:
        score = self._get_score()
        self.status = f"confidence={score:.3f} threshold={self.threshold}"
        if score >= self.threshold:
            return self.data
        self.stop("high_confidence")
        return self.data

    def low_confidence(self) -> Data:
        score = self._get_score()
        if score < self.threshold:
            return self.data
        self.stop("low_confidence")
        return self.data
