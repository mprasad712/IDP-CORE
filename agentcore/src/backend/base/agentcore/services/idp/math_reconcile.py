from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage

from agentcore.services.idp.extraction import StructuredExtractionResult, _extract_usage_from_response


def parse_float(val: Any) -> float | None:
    """Safely parse a value into a float, handling currency symbols, separators, and spaces."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return None
    
    # Remove currency symbols and non-numeric/separator characters
    cleaned = ""
    for char in val_str:
        if char.isdigit() or char in ".-+":
            cleaned += char
        elif char == ",":
            cleaned += char
            
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # Comma is decimal separator (e.g. 1.234,56)
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Period is decimal separator (e.g. 1,234.56)
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Check if comma is decimal or thousands separator
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
            
    try:
        return float(cleaned)
    except ValueError:
        return None


async def reconcile_math(
    extracted: dict,
    llm_model: Any,
    *,
    max_attempts: int = 2,
    tolerance: float = 0.01,
    ocr_text: str | None = None,
) -> dict:
    """Identify arithmetic mismatches between totals and line items, re-prompting the LLM to resolve them.

    Args:
        extracted: The extracted JSON dictionary matching StructuredExtractionResult.
        llm_model: The LLM model to use for corrections.
        max_attempts: Max correction retry attempts.
        tolerance: Allowed discrepancy float limit.
        ocr_text: Optional layout-reconstructed OCR text for context.

    Returns:
        A dict matching {"extracted", "attempts", "balanced", "report"}.
    """
    # Normalize input
    if isinstance(extracted, StructuredExtractionResult):
        current_extracted = extracted.model_dump()
    elif isinstance(extracted, dict):
        try:
            current_extracted = StructuredExtractionResult.model_validate(extracted).model_dump()
        except Exception as e:
            logger.warning(f"[Math Reconcile] input dictionary failed model validation: {e}")
            current_extracted = extracted
    else:
        current_extracted = extracted

    # Configurable candidate keys
    total_keys = ["total_amount", "total", "invoice_total", "grand_total", "amount_due", "total_due", "net_amount", "subtotal"]
    amount_col_keys = ["line_amount", "amount", "line_total", "total", "item_total", "subtotal", "price"]

    # Helper function to compute mathematical values
    def evaluate_balance(data: dict) -> tuple[float | None, str | None, float, bool]:
        headers = data.get("headers", {})
        total_val = None
        total_key = None
        for tk in total_keys:
            if tk in headers and headers[tk].get("value") is not None:
                parsed = parse_float(headers[tk]["value"])
                if parsed is not None:
                    total_key = tk
                    total_val = parsed
                    break

        line_items = data.get("line_items", [])
        line_sums = 0.0
        has_lines = False
        for row in line_items:
            row_val = None
            for col in row.get("columns", []):
                if col.get("column_name") in amount_col_keys and col.get("value") is not None:
                    parsed = parse_float(col["value"])
                    if parsed is not None:
                        row_val = parsed
                        break
            if row_val is not None:
                line_sums += row_val
                has_lines = True

        if total_val is not None and has_lines:
            diff = abs(total_val - line_sums)
            balanced = diff <= tolerance
            return total_val, total_key, line_sums, balanced
        
        # If total or lines are missing/not numeric, we consider it balanced (or check skipped)
        return total_val, total_key, line_sums, True

    # Initial evaluation
    total_val, total_key, line_sums, is_balanced = evaluate_balance(current_extracted)
    
    discrepancy_logs = []
    if not is_balanced:
        msg = f"Discrepancy detected: Header Total '{total_key}' is {total_val:.2f}, but sum of line items is {line_sums:.2f} (diff = {abs(total_val - line_sums):.2f})"
        discrepancy_logs.append(msg)
        logger.info(f"[Math Reconcile] {msg}")
    else:
        discrepancy_logs.append("Initial extraction is mathematically balanced.")

    attempts_run = 0
    aggregated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    while not is_balanced and attempts_run < max_attempts:
        attempts_run += 1
        discrepancy_str = discrepancy_logs[-1]

        system_prompt = (
            "You are a mathematical reconciliation assistant.\n"
            "An extraction process extracted headers and line items from a document, but there is a mathematical discrepancy:\n"
            f"{discrepancy_str}\n\n"
            "Please review the extracted values and either correct the header total or the line item amounts so they balance mathematically.\n"
            "If the document has OCR text, inspect it to see if any values were extracted incorrectly.\n"
            "Ensure you return the complete corrected extraction data following the original schema."
        )

        user_prompt = f"Original Extracted Data:\n{json.dumps(current_extracted, indent=2)}"
        if ocr_text:
            user_prompt += f"\n\nDocument OCR Text Context:\n{ocr_text[:6000]}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]

        result = None
        usage_info = None
        if hasattr(llm_model, "with_structured_output"):
            try:
                try:
                    structured_model = llm_model.with_structured_output(StructuredExtractionResult, include_raw=True)
                    res_dict = await structured_model.ainvoke(messages)
                    if isinstance(res_dict, dict):
                        result = res_dict.get("parsed")
                        raw_response = res_dict.get("raw")
                    else:
                        result = res_dict
                        raw_response = None
                except TypeError:
                    structured_model = llm_model.with_structured_output(StructuredExtractionResult)
                    result = await structured_model.ainvoke(messages)
                    raw_response = None
                usage_info = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
            except Exception as e:
                logger.warning(f"[Math Reconcile] structured output failed: {e}. Falling back to standard JSON parsing.")

        if result is None:
            try:
                response = await llm_model.ainvoke(messages)
                usage_info = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))
                content = response.content if hasattr(response, "content") else str(response)
                content = content.strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    if lines[0].strip().startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                parsed = json.loads(content)
                result = StructuredExtractionResult.model_validate(parsed)
            except Exception as e:
                logger.error(f"[Math Reconcile] prediction parsing failed: {e}")
                break

        if usage_info:
            aggregated_usage["input_tokens"] += usage_info.get("input_tokens", 0)
            aggregated_usage["output_tokens"] += usage_info.get("output_tokens", 0)
            aggregated_usage["total_tokens"] += usage_info.get("total_tokens", 0)

        if result:
            current_extracted = result.model_dump()
            total_val, total_key, line_sums, is_balanced = evaluate_balance(current_extracted)
            if is_balanced:
                discrepancy_logs.append(f"Balanced successfully on attempt {attempts_run}!")
                logger.info(f"[Math Reconcile] Balanced successfully on attempt {attempts_run}.")
            else:
                msg = f"Attempt {attempts_run} failed to reconcile: Total '{total_key}' is {total_val:.2f}, but sum of line items is {line_sums:.2f} (diff = {abs(total_val - line_sums):.2f})"
                discrepancy_logs.append(msg)
                logger.info(f"[Math Reconcile] {msg}")

    return {
        "extracted": current_extracted,
        "attempts": attempts_run,
        "balanced": is_balanced,
        "report": discrepancy_logs,
        "_usage": aggregated_usage if aggregated_usage["total_tokens"] > 0 else None,
    }
