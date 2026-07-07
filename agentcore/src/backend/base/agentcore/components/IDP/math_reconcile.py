from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import FloatInput, HandleInput, IntInput, Output
from agentcore.schema.message import Message


class IDPMathReconcile(Node):
    """Validate the extracted fields' arithmetic and re-prompt the model to fix mismatches (real work
    — wraps ``services/idp/math_reconcile.reconcile_math``)."""

    display_name = "Math Reconcile"
    description = "Validates extraction arithmetic and re-prompts the LLM when a mismatch is detected."
    icon = "Calculator"
    name = "IDPMathReconcile"

    inputs = [
        HandleInput(name="data", display_name="Extracted Data", input_types=["Data", "Message"], required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=False),
        FloatInput(name="tolerance", display_name="Tolerance", value=0.01, advanced=True),
        IntInput(name="max_retries", display_name="Max Retries", value=2, advanced=True),
    ]
    outputs = [Output(display_name="Validated Data", name="validated_data", method="reconcile")]

    async def reconcile(self) -> Message:
        from loguru import logger

        from agentcore.services.idp.graph_native.payload import carry, effective_payload
        from agentcore.services.idp.math_reconcile import reconcile_math

        payload = effective_payload(self, self.data)
        extracted = payload.get("extracted")
        if not isinstance(extracted, dict) or not extracted.get("headers"):
            self.status = "nothing to reconcile"
            return carry(self.data)

        ocr_text = getattr(self.data, "text", None) or None
        try:
            reconciled = await reconcile_math(
                extracted, self.llm,
                max_attempts=int(self.max_retries or 2),
                tolerance=float(self.tolerance or 0.01),
                ocr_text=ocr_text if (ocr_text and ocr_text.strip()) else None,
            )
            if isinstance(reconciled, dict):
                reconciled.pop("_usage", None)
            self.status = "reconciled"
            return carry(self.data, extracted=reconciled if isinstance(reconciled, dict) else extracted)
        except Exception as exc:  # reconcile is best-effort — never fail the document
            logger.warning(f"[IDP native] math reconcile skipped: {exc}")
            self.status = f"reconcile skipped ({exc})"
            return carry(self.data)
