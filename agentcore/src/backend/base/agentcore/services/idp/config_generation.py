"""Generate a DRAFT field configuration (headers + line items + per-field prompts) from a
plain-language description, using an LLM meta-prompt.

Returns a draft only — persistence is done by the existing create-config endpoint
(``POST /api/v1/idp/field-configs/``). Pure of HTTP/DB so it is unit-testable with a fake LLM.
Mirrors the structured-output + raw-JSON-fallback pattern in ``services/idp/extraction.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

_HEADER_TYPES = {"text", "number", "date", "boolean"}
_LINE_TYPES = {"text", "number", "date"}
_TYPE_ALIASES = {
    "string": "text", "str": "text", "int": "number", "integer": "number",
    "float": "number", "decimal": "number", "currency": "number", "amount": "number",
    "bool": "boolean", "datetime": "date",
}
_MAX_HEADERS = 40
_MAX_LINE_ITEMS = 30


class ConfigGenerationError(Exception):
    """The model returned no usable schema (empty / unparseable)."""


class _SuggestedHeader(BaseModel):
    field_name: str
    field_type: str = "text"
    is_required: bool = False
    prompt: str | None = None


class _SuggestedLineItem(BaseModel):
    column_name: str
    column_type: str = "text"
    is_required: bool = False
    prompt: str | None = None


class SuggestedConfig(BaseModel):
    suggested_name: str = Field(default="Untitled Configuration")
    headers: list[_SuggestedHeader] = Field(default_factory=list)
    line_items: list[_SuggestedLineItem] = Field(default_factory=list)


def _coerce_type(value: str | None, allowed: set[str]) -> str:
    v = (value or "text").strip().lower()
    v = _TYPE_ALIASES.get(v, v)
    return v if v in allowed else "text"


def _normalize(suggested: SuggestedConfig) -> dict[str, Any]:
    """Coerce types, drop empties/dupes (case-insensitive), cap counts, assign 1-based order."""
    headers: list[dict] = []
    seen_h: set[str] = set()
    for h in suggested.headers:
        name = (h.field_name or "").strip()
        key = name.lower()
        if not name or key in seen_h:
            continue
        seen_h.add(key)
        headers.append({
            "field_name": name,
            "field_type": _coerce_type(h.field_type, _HEADER_TYPES),
            "is_required": bool(h.is_required),
            "prompt": (h.prompt or None),
            "display_order": len(headers) + 1,
        })
        if len(headers) >= _MAX_HEADERS:
            break

    line_items: list[dict] = []
    seen_l: set[str] = set()
    for li in suggested.line_items:
        name = (li.column_name or "").strip()
        key = name.lower()
        if not name or key in seen_l:
            continue
        seen_l.add(key)
        line_items.append({
            "column_name": name,
            "column_type": _coerce_type(li.column_type, _LINE_TYPES),
            "is_required": bool(li.is_required),
            "prompt": (li.prompt or None),
            "display_order": len(line_items) + 1,
        })
        if len(line_items) >= _MAX_LINE_ITEMS:
            break

    name = (suggested.suggested_name or "").strip() or "Untitled Configuration"
    return {"suggested_name": name, "headers": headers, "line_items": line_items}


_SYSTEM = (
    "You are a document-extraction schema designer. Given a description of a document type, "
    "propose the fields to extract. Return a JSON object with: suggested_name (string), "
    "headers (list of {field_name, field_type, is_required, prompt}), and line_items "
    "(list of {column_name, column_type, is_required, prompt}). field_type/column_type must be "
    "one of text, number, date, boolean (line items: text, number, date). 'prompt' is a short "
    "instruction telling an LLM how to find that single field. Header fields are document-level "
    "(invoice number, date, vendor); line_items are the repeating table columns. Return ONLY JSON."
)


def _user_prompt(description: str, sample_text: str | None) -> str:
    parts = [f"Document description:\n{description.strip()}"]
    if sample_text and sample_text.strip():
        parts.append(
            "A sample of the document's text (use it to choose realistic fields):\n"
            + sample_text.strip()[:6000]
        )
    parts.append("Design the extraction schema now.")
    return "\n\n".join(parts)


def _parse_raw(content: Any) -> SuggestedConfig:
    text = content if isinstance(content, str) else getattr(content, "content", "") or str(content)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ConfigGenerationError("model did not return JSON")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ConfigGenerationError(f"could not parse model JSON: {e}") from e
    try:
        return SuggestedConfig.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise ConfigGenerationError(f"model JSON did not match schema: {e}") from e


async def generate_field_config(description: str, sample_text: str | None, llm_model: Any) -> dict[str, Any]:
    """Return a normalized DRAFT config dict.

    Raises ConfigGenerationError if the description is empty or the model yields no header
    fields (a config with zero headers is not usable).
    """
    if not (description or "").strip():
        raise ConfigGenerationError("description is required")

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(description, sample_text)},
    ]

    suggested: SuggestedConfig | None = None
    if hasattr(llm_model, "with_structured_output"):
        try:
            result = await llm_model.with_structured_output(SuggestedConfig).ainvoke(messages)
            if isinstance(result, SuggestedConfig):
                suggested = result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[config-gen] structured output failed: {e}; falling back to raw JSON")

    if suggested is None:
        response = await llm_model.ainvoke(messages)
        suggested = _parse_raw(response)

    out = _normalize(suggested)
    if not out["headers"]:
        raise ConfigGenerationError("the model proposed no header fields")
    return out
