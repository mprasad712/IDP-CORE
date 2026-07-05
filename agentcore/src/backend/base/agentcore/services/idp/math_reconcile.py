from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional
from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage

from agentcore.services.idp.extraction import StructuredExtractionResult, _extract_usage_from_response


# Identified (total_field, amount_column) cached per extraction SHAPE (the set of field names),
# so a given Field Configuration is classified by the model ONCE and reused for every document.
_FIELD_ID_CACHE: dict[tuple, tuple[Optional[str], Optional[str]]] = {}

_IDENTIFY_SYSTEM = (
    "You map extracted document fields to their arithmetic roles for reconciliation. "
    "Respond with ONLY a JSON object, no prose."
)


def _first_json_object(raw: str) -> dict:
    """Parse the first JSON object out of a model response (tolerant of fences / prose)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
    return {}


async def _identify_fields(extracted: dict, llm_model: Any) -> tuple[Optional[str], Optional[str]]:
    """Ask the model which header field is the reconciliation total and which line-item column is
    the per-row amount to sum. Returns ``(total_field, amount_column)`` — either may be None.

    Cached per set of field names (≈ per Field Configuration): runs once, then reused. This is the
    config-driven replacement for a hardcoded key list — it works for ANY field naming.
    """
    headers = extracted.get("headers", {}) or {}
    line_items = extracted.get("line_items", []) or []
    header_names = sorted(headers.keys())
    column_names = sorted({
        c.get("column_name")
        for row in line_items
        for c in (row.get("columns", []) or [])
        if c.get("column_name")
    })
    if not header_names or not column_names:
        return None, None

    cache_key = (tuple(header_names), tuple(column_names))
    if cache_key in _FIELD_ID_CACHE:
        return _FIELD_ID_CACHE[cache_key]

    header_lines = "\n".join(f"  {n} = {(headers.get(n) or {}).get('value')}" for n in header_names)
    user = (
        "Header fields (name = sample value):\n" + header_lines + "\n\n"
        "Line-item columns: " + ", ".join(column_names) + "\n\n"
        "Return ONLY this JSON:\n"
        '{"total_field": <the header field holding the document total to reconcile against the sum '
        'of the line amounts, or null>, "amount_column": <the line-item column holding each row\'s '
        "amount to sum, or null>}\n"
        "Prefer a pre-tax / taxable / net total when one exists (it matches the sum of line net "
        "amounts). If nothing applies, use null."
    )
    try:
        resp = await llm_model.ainvoke([SystemMessage(content=_IDENTIFY_SYSTEM), HumanMessage(content=user)])
        raw = resp.content if hasattr(resp, "content") else str(resp)
        if not isinstance(raw, str):
            raw = json.dumps(raw)
        obj = _first_json_object(raw)

        def _clean(v):
            v = str(v).strip() if v is not None else ""
            return v if v and v.lower() not in ("null", "none") else None

        tf = _clean(obj.get("total_field"))
        ac = _clean(obj.get("amount_column"))
        if tf not in headers:          # only trust names that actually exist
            tf = None
        if ac not in column_names:
            ac = None
        result = (tf, ac)
        logger.info(f"[Math Reconcile] identified total_field={tf!r}, amount_column={ac!r}")
    except Exception as e:
        logger.warning(f"[Math Reconcile] field identification failed: {e}")
        result = (None, None)

    _FIELD_ID_CACHE[cache_key] = result
    return result


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
    page_images: list | None = None,
) -> dict:
    """Detect and fix arithmetic mismatches between the document total and its line items.

    The total field + line-amount column are identified by the model (``_identify_fields``, cached
    per config) — no hardcoded key list. The comparison itself is done deterministically in code.
    On a mismatch the model is re-prompted to correct the values; for scanned/vision docs the page
    image(s) are attached so it can RE-READ the true value instead of only juggling numbers.

    Args:
        extracted: The extracted JSON dictionary matching StructuredExtractionResult.
        llm_model: The LLM model to use for identification + corrections.
        max_attempts: Max correction retry attempts.
        tolerance: Allowed discrepancy float limit.
        ocr_text: Optional OCR text (text-route docs) used as correction context.
        page_images: Optional ``[(png_bytes, mime), ...]`` (vision-route docs) attached to the fix.

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

    # Identify the total field + line-amount column with the model (cached ≈ per config). Falls
    # back to a minimal keyword guess only if identification returns nothing, so nothing regresses.
    id_total, id_amount = await _identify_fields(current_extracted, llm_model)
    fallback_total = ["invoice_taxable_value", "taxable_value", "net_amount", "subtotal",
                      "total_amount", "total", "invoice_total", "grand_total", "invoice_value",
                      "amount_due", "total_due"]
    fallback_amount = ["line_amount", "amount", "line_total", "net_worth", "item_total", "total", "value", "price"]

    def evaluate_balance(data: dict) -> tuple[float | None, str | None, float, bool]:
        headers = data.get("headers", {}) or {}

        # total: the identified field first, else the first present fallback key
        total_key = None
        total_val = None
        for tk in ([id_total] if id_total else []) + fallback_total:
            if tk and tk in headers and (headers[tk] or {}).get("value") is not None:
                parsed = parse_float(headers[tk]["value"])
                if parsed is not None:
                    total_key, total_val = tk, parsed
                    break

        # amount: use ONLY the identified column when known; else scan the fallback list per row
        amount_cols = [id_amount] if id_amount else fallback_amount
        line_sums = 0.0
        has_lines = False
        for row in data.get("line_items", []) or []:
            for col in row.get("columns", []) or []:
                if col.get("column_name") in amount_cols and col.get("value") is not None:
                    parsed = parse_float(col["value"])
                    if parsed is not None:
                        line_sums += parsed
                        has_lines = True
                        break

        if total_val is not None and has_lines:
            return total_val, total_key, line_sums, abs(total_val - line_sums) <= tolerance
        # No recognizable total or amounts -> nothing to check -> treat as balanced (skip).
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

        if page_images:
            source_hint = (
                "The document page image(s) are attached. Re-read the page to find the CORRECT "
                "value and fix whichever value was extracted incorrectly (do not just change a "
                "number to make it add up)."
            )
        elif ocr_text:
            source_hint = "Inspect the OCR text below to find any value that was extracted incorrectly."
        else:
            source_hint = "Recompute from the numbers so the total equals the sum of the line amounts."

        system_prompt = (
            "You are a document arithmetic reconciliation assistant.\n"
            "An extraction has a mathematical discrepancy between the header total and the sum of "
            f"the line-item amounts:\n{discrepancy_str}\n\n"
            f"{source_hint}\n"
            "Return the COMPLETE corrected extraction data following the original schema."
        )

        user_prompt = f"Original Extracted Data:\n{json.dumps(current_extracted, indent=2)}"
        if ocr_text and not page_images:
            user_prompt += f"\n\nDocument OCR Text Context:\n{ocr_text[:6000]}"

        if page_images:
            # Multimodal correction: text + the page image(s) so the model can re-read the doc.
            human_content: list = [{"type": "text", "text": user_prompt}]
            for img_bytes, mime in page_images[:3]:
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                human_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            human_message = HumanMessage(content=human_content)
        else:
            human_message = HumanMessage(content=user_prompt)

        messages = [
            SystemMessage(content=system_prompt),
            human_message,
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
