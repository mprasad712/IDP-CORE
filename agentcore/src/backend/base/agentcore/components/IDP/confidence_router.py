from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, DataInput, FloatInput, MessageTextInput, Output
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
        BoolInput(
            name="require_grounding",
            display_name="Require values to appear in the document",
            value=False,
            info=(
                "Send a document to Low Confidence when the model produced a value that is NOT anywhere in "
                "the document — even if it reported high confidence. That is the shape of a hallucination: "
                "the model is confident precisely because it believes what it invented. Ignored when there "
                "is no text layer to check against (vision runs). Off by default: turning it on changes "
                "which branch documents take, so enable it, test, and republish."
            ),
        ),
    ]

    outputs = [
        Output(display_name="High Confidence", name="high_confidence", method="high_confidence", group_outputs=True),
        Output(display_name="Low Confidence", name="low_confidence", method="low_confidence", group_outputs=True),
    ]

    def _get_score(self) -> float:
        from agentcore.services.idp.graph_native.payload import overall_confidence, resolve_field

        # The mean of the MODEL's per-field confidences — the same number `save_extraction_results` stores
        # and the UI displays. The extractor emits a Message whose confidence rides in
        # additional_kwargs['idp'] — NOT as an attribute, so the old getattr(Message, 'confidence') always
        # returned None and routed everything to 'low'.
        score = overall_confidence(self.data, component=self)
        if score is None:
            raw = resolve_field(self.data, (self.confidence_field or "confidence").strip(), component=self)
            try:
                score = float(raw)
            except (TypeError, ValueError):
                score = 0.0
        return score

    def _decide(self) -> tuple:
        """``(score, ungrounded, takes_high)``. Both output methods MUST agree, so decide once.

        Confidence and grounding are separate questions and are asked separately. Averaging a "not on the
        page" penalty into the score — which is what the old OCR-evidence formula did — makes a confident
        hallucination indistinguishable from a low-confidence honest read.

        ``require_grounding`` is OFF by default, and that is deliberate. ``Node`` materializes every declared
        input as an attribute with its class default, so a node rebuilt from a snapshot published before this
        input existed is indistinguishable from a fresh one — a `True` default would silently change which
        branch every already-deployed agent's documents take, with no republish. Enable it on the canvas.
        """
        from agentcore.services.idp.graph_native.payload import ungrounded_fields

        score = self._get_score()
        ungrounded = ungrounded_fields(self.data, component=self) if getattr(self, "require_grounding", False) else []
        return score, ungrounded, (score >= self.threshold and not ungrounded)

    def high_confidence(self) -> Data:
        from agentcore.services.idp.graph_native.payload import carry

        score, ungrounded, takes_high = self._decide()
        if ungrounded:
            shown = ", ".join(ungrounded[:3]) + (" …" if len(ungrounded) > 3 else "")
            self.status = f"confidence={score:.3f} · {len(ungrounded)} value(s) not in the document: {shown}"
        else:
            self.status = f"confidence={score:.3f} threshold={self.threshold}"
        if takes_high:
            # Stash the computed score as overall_conf so a downstream Rules / Approval node resolves the
            # SAME confidence value (not the classifier's, or None) when a rule checks 'confidence'.
            return carry(self.data, overall_conf=round(float(score), 4))
        self.stop("high_confidence")
        return self.data

    def low_confidence(self) -> Data:
        from agentcore.services.idp.graph_native.payload import carry

        score, ungrounded, takes_high = self._decide()
        if not takes_high:
            return carry(self.data, overall_conf=round(float(score), 4), ungrounded_fields=ungrounded)
        self.stop("low_confidence")
        return self.data
