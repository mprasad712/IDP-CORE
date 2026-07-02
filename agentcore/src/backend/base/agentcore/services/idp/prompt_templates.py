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


COMPACT_EXTRACTION_SYSTEM_TEMPLATE = (
    "You are an expert Intelligent Document Processing (IDP) agent and data extraction specialist.\n"
    "Extract the requested fields from the document text and return ONLY a JSON object of this shape:\n"
    '{"headers": {"field_name": "value_or_null", ...}, "line_items": [{"column_name": "value", ...}, ...]}\n\n'
    "Rules:\n"
    "- 'headers' are single document-level fields; 'line_items' is a list of table rows, each a flat "
    "object of column_name -> value.\n"
    "- Extract EVERY row of the table — do not stop early or summarise.\n"
    "- Include ONLY the fields/columns listed below that you actually find; omit anything not present "
    "(do NOT invent values).\n"
    "- Dates as YYYY-MM-DD; numbers as plain strings. Values must be strings or null.\n"
    "- Do NOT include any confidence or reasoning keys. Return ONLY valid JSON — no markdown, no prose, "
    "no code fences."
)

COMPACT_EXTRACTION_USER_TEMPLATE = """\
Extract the value of each field listed below from the document text.

### Header Fields to Extract:
{header_field_descriptions}

### Line Item Columns (one object per table row):
{line_item_descriptions}

### Document:
-------
{data}
-------

Return ONLY a JSON object of exactly this shape (flat values, no confidence/reasoning):

{json_schema}\
"""


def build_compact_extraction_messages(
    headers: List[Any],
    line_items: List[Any],
    ocr_text: str,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for COMPACT named-config extraction.

    Mirrors :func:`build_extraction_messages` but asks the model for FLAT key/value JSON only
    (no per-cell ``confidence``/``reasoning``). The verbose per-cell schema overflows the model's
    output-token budget on multi-row tables and truncates ``line_items`` to empty; the compact
    shape is ~half the tokens, and a uniform default confidence is re-attached downstream by
    ``extraction._expand_extraction``. Per-field DB prompts/descriptions are still passed as hints.
    """
    if headers:
        header_field_descriptions = "\n".join(
            _field_description(h.field_name, h.field_type, getattr(h, "prompt", None), getattr(h, "description", None))
            for h in headers
        )
    else:
        header_field_descriptions = "None"

    if line_items:
        line_item_descriptions = "\n".join(
            _field_description(getattr(c, "column_name", ""), getattr(c, "column_type", "text"), getattr(c, "prompt", None), None)
            for c in line_items
        )
    else:
        line_item_descriptions = "None"

    # Flat schema scaffold — header_name -> placeholder, one sample row of column_name -> placeholder.
    header_schema = {h.field_name: "<value or null>" for h in headers}
    line_items_schema = (
        [{c.column_name: "<value>" for c in line_items}] if line_items else []
    )
    json_schema = json.dumps({"headers": header_schema, "line_items": line_items_schema}, indent=2)

    user_prompt = COMPACT_EXTRACTION_USER_TEMPLATE.format(
        header_field_descriptions=header_field_descriptions,
        line_item_descriptions=line_item_descriptions,
        data=ocr_text,
        json_schema=json_schema,
    )
    return COMPACT_EXTRACTION_SYSTEM_TEMPLATE, user_prompt


COMPACT_VISION_SYSTEM_TEMPLATE = (
    "You are an expert Intelligent Document Processing (IDP) agent and data extraction specialist.\n"
    "You are given the document as one or more page IMAGES. Read the values directly from the "
    "image(s) — there is no OCR text to rely on.\n"
    "Extract the requested fields and return ONLY a JSON object of this shape:\n"
    '{"headers": {"field_name": {"value": "value_or_null", "confidence": 0.95}, ...},\n'
    ' "line_items": [{"column_name": {"value": "value", "confidence": 0.9}, ...}, ...]}\n\n'
    "Rules:\n"
    "- 'headers' are single document-level fields; 'line_items' is a list of table rows, each a flat "
    "object of column_name -> {value, confidence}.\n"
    "- Extract EVERY row of the table — do not stop early or summarise.\n"
    "- 'confidence' MUST be a genuine float 0.0–1.0 reflecting how clearly you can read the value "
    "from the image (0.90+ = crisp and unambiguous; lower = blurry, partially occluded, or inferred).\n"
    "- If a field is not present: value null, confidence 0.0. Do NOT invent values.\n"
    "- Dates as YYYY-MM-DD; numbers as plain strings. Values must be strings or null.\n"
    "- Return ONLY valid JSON — no markdown, no prose, no code fences."
)

COMPACT_VISION_USER_TEMPLATE = """\
Extract the value and a confidence score for each field listed below, reading directly from the document image(s) provided in this message.

### Header Fields to Extract:
{header_field_descriptions}

### Line Item Columns (one object per table row):
{line_item_descriptions}

### Document:
The document is provided as page image(s) in this message. Read the values directly from the image(s).

Return ONLY a JSON object of exactly this shape:

{json_schema}\
"""


def build_compact_extraction_messages_vision(
    headers: List[Any],
    line_items: List[Any],
) -> tuple[str, str]:
    """(system, user) for COMPACT NAMED-CONFIG extraction via VISION (page images, no OCR text).

    Unlike :func:`build_compact_extraction_messages` (which SUPPRESSES confidence because the raw
    OCR token stream re-scores each value downstream in ``_ocr_evidence``), the vision path has NO
    OCR tokens to score against — so this variant explicitly asks the model for a genuine per-field
    ``confidence`` and that score is what gets persisted. The document itself is supplied as image
    blocks by the caller (``extract_vision``); this only builds the text instruction + schema.
    """
    if headers:
        header_field_descriptions = "\n".join(
            _field_description(h.field_name, h.field_type, getattr(h, "prompt", None), getattr(h, "description", None))
            for h in headers
        )
    else:
        header_field_descriptions = "None"

    if line_items:
        line_item_descriptions = "\n".join(
            _field_description(getattr(c, "column_name", ""), getattr(c, "column_type", "text"), getattr(c, "prompt", None), None)
            for c in line_items
        )
    else:
        line_item_descriptions = "None"

    header_schema = {h.field_name: {"value": "<value or null>", "confidence": 0.95} for h in headers}
    line_items_schema = (
        [{c.column_name: {"value": "<value>", "confidence": 0.9} for c in line_items}] if line_items else []
    )
    json_schema = json.dumps({"headers": header_schema, "line_items": line_items_schema}, indent=2)

    user_prompt = COMPACT_VISION_USER_TEMPLATE.format(
        header_field_descriptions=header_field_descriptions,
        line_item_descriptions=line_item_descriptions,
        json_schema=json_schema,
    )
    return COMPACT_VISION_SYSTEM_TEMPLATE, user_prompt


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
