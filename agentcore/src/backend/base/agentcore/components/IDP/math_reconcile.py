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
            result = await reconcile_math(
                extracted, self.llm,
                max_attempts=int(self.max_retries or 2),
                tolerance=float(self.tolerance or 0.01),
                ocr_text=ocr_text if (ocr_text and ocr_text.strip()) else None,
            )
            # reconcile_math returns a WRAPPER: {"extracted": {...headers, line_items...}, "attempts",
            # "balanced", "report", "_usage"}. Pull the INNER extraction out — the sink expects
            # extracted = {headers, line_items}, NOT the wrapper (otherwise it finds no headers and saves
            # 0). Fall back to the ORIGINAL extraction when the fix produced nothing usable (e.g. the
            # re-prompt hit a quota / parse error) so Math Reconcile NEVER drops the extracted fields.
            fixed = result.get("extracted") if isinstance(result, dict) else None
            final = (
                fixed
                if (isinstance(fixed, dict) and (fixed.get("headers") or fixed.get("line_items")))
                else extracted
            )
            balanced = bool(result.get("balanced")) if isinstance(result, dict) else False
            self.status = "reconciled (balanced)" if balanced else "mismatch remains — kept extraction"
            return carry(self.data, extracted=final)
        except Exception as exc:  # reconcile is best-effort — never fail the document
            logger.warning(f"[IDP native] math reconcile skipped: {exc}")
            self.status = f"reconcile skipped ({exc}) — kept extraction"
            return carry(self.data, extracted=extracted)
