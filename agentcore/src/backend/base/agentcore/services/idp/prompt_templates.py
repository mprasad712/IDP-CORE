"""
General-purpose LLM prompt templates for IDP extraction.

Placeholders filled at runtime from the selected Field Configuration:
  {header_field_descriptions}  — per-field name + prompt from DB
  {line_item_descriptions}     — per-column name + prompt from DB
  {data}                       — raw OCR text of the document
  {json_schema}                — expected output JSON scaffold (field names injected)
"""

import json
from typing import Any, List

# ──────────────────────────────────────────────────────────────────────────────
# Template strings
# ──────────────────────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_TEMPLATE = (
    "You are an expert Intelligent Document Processing (IDP) agent and data extraction specialist.\n"
    "Your task is to extract structured data from the document text provided, "
    "conforming exactly to the field definitions supplied by the user.\n\n"
    "For EVERY extracted field you must provide:\n"
    "  - \"value\": the extracted string value (or null if not found)\n"
    "  - \"confidence\": decimal confidence score 0.0–1.0 (1.0 = fully certain)\n"
    "  - \"reasoning\": a brief direct evidence snippet from the document\n\n"
    "Return ONLY valid JSON matching the provided schema. No markdown, no extra text."
)

EXTRACTION_USER_TEMPLATE = """\
You are given a document. Your task is to extract the value of each field listed below and assign a confidence score to each field, reflecting how confident you are that the extracted data is correct.

### Instructions:
- Extract the value of each field from the document text.
- For each field, assign a confidence score as a decimal (0.0 to 1.0), where 1.0 means fully confident and lower values reflect less certainty.
- Provide brief reasoning — a direct snippet or evidence from the document text.
- If a field is not found in the document, set value to null and confidence to 0.0.
- For dates, return in YYYY-MM-DD format where possible.
- For numbers, return numeric values as strings.
- For boolean fields, return "true" or "false".

### Header Fields to Extract:
{header_field_descriptions}

### Line Item Columns (Table):
{line_item_descriptions}

### Document:
-------
{data}
-------

Provide the extracted data in the following JSON format only. Do not add any extra key-value pairs in your response.

{json_schema}

Note: Your answer should only contain the JSON — no markdown formatting or extra text in the response.\
"""


# ──────────────────────────────────────────────────────────────────────────────
# Builder helpers
# ──────────────────────────────────────────────────────────────────────────────

def _field_description(name: str, field_type: str, prompt: str | None, description: str | None) -> str:
    """Return a single line describing one field for the LLM."""
    detail = prompt or description or f"Extract the {name} value from the document."
    return f"{name} ({field_type}): {detail}"


def build_extraction_messages(
    headers: List[Any],
    line_items: List[Any],
    ocr_text: str,
) -> tuple[str, str]:
    """
    Build (system_prompt, user_prompt) for the extraction LLM call.

    `headers`    — list of IdpFieldConfigHeader ORM objects
    `line_items` — list of IdpFieldConfigLineItem ORM objects
    `ocr_text`   — raw OCR output of the document

    Returns a (system, user) string tuple ready to wrap in LangChain messages.
    """
    # 1. Field descriptions block
    if headers:
        header_desc_lines = [
            _field_description(h.field_name, h.field_type, getattr(h, "prompt", None), getattr(h, "description", None))
            for h in headers
        ]
        header_field_descriptions = "\n".join(header_desc_lines)
    else:
        header_field_descriptions = "None"

    if line_items:
        line_item_desc_lines = [
            _field_description(
                getattr(c, "column_name", ""),
                getattr(c, "column_type", "text"),
                getattr(c, "prompt", None),
                None,
            )
            for c in line_items
        ]
        line_item_descriptions = "\n".join(line_item_desc_lines)
    else:
        line_item_descriptions = "None"

    # 2. JSON schema scaffold (shows LLM exactly which keys to produce)
    header_schema: dict = {
        h.field_name: {"value": "<string or null>", "confidence": "<0.0-1.0>", "reasoning": "<evidence>"}
        for h in headers
    }

    if line_items:
        sample_columns = [
            {"column_name": c.column_name, "value": "<string or null>", "confidence": "<0.0-1.0>", "reasoning": "<evidence>"}
            for c in line_items
        ]
        line_items_schema: list = [{"row_index": 0, "columns": sample_columns}]
    else:
        line_items_schema = []

    json_schema = json.dumps(
        {"headers": header_schema, "line_items": line_items_schema},
        indent=2,
    )

    # 3. Fill templates
    system_prompt = EXTRACTION_SYSTEM_TEMPLATE

    user_prompt = EXTRACTION_USER_TEMPLATE.format(
        header_field_descriptions=header_field_descriptions,
        line_item_descriptions=line_item_descriptions,
        data=ocr_text,
        json_schema=json_schema,
    )

    return system_prompt, user_prompt
