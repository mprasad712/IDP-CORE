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
        from agentcore.services.idp.graph_native.payload import overall_confidence, resolve_field

        # Prefer the overall extraction confidence derived from the IDP payload's extracted fields
        # (same mean-of-per-field basis as save_extraction_results). The extractor emits a Message
        # whose confidence rides in additional_kwargs['idp'] — NOT as an attribute, so the old
        # getattr(Message, 'confidence') always returned None and routed everything to 'low'.
        score = overall_confidence(self.data, component=self)
        if score is None:
            raw = resolve_field(self.data, (self.confidence_field or "confidence").strip(), component=self)
            try:
                score = float(raw)
            except (TypeError, ValueError):
                score = 0.0
        return score

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
