import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from agentcore.services.database.models.idp.config import (
    IdpFieldConfiguration,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
)
from agentcore.services.idp.prompt_templates import (
    build_extraction_messages,
    build_compact_extraction_messages,
)

# ──────────────────────────────────────────────────────────────────────
# Pydantic Schemas for Structured Output
# ──────────────────────────────────────────────────────────────────────

class ExtractedHeaderField(BaseModel):
    value: Optional[str] = Field(
        default=None,
        description="The extracted value for the header field, or null/None if not found/applicable."
    )
    confidence: float = Field(
        default=0.0,
        description="Confidence score (0.0 to 1.0) indicating how sure the model is about this extraction."
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Reasoning or direct source snippet from the document justifying this extraction."
    )

class ExtractedLineItemColumn(BaseModel):
    column_name: str = Field(description="The name of the column.")
    value: Optional[str] = Field(
        default=None,
        description="The extracted value for this column, or null/None if not found/applicable."
    )
    confidence: float = Field(
        default=0.0,
        description="Confidence score (0.0 to 1.0) indicating how sure the model is about this extraction."
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Reasoning or direct source snippet from the document justifying this extraction."
    )

class ExtractedLineItemRow(BaseModel):
    row_index: int = Field(description="0-based index of the row.")
    columns: List[ExtractedLineItemColumn] = Field(
        description="List of columns extracted for this row."
    )

class StructuredExtractionResult(BaseModel):
    headers: Dict[str, ExtractedHeaderField] = Field(
        default_factory=dict,
        description="Dictionary of header fields (keys are field names)."
    )
    line_items: List[ExtractedLineItemRow] = Field(
        default_factory=list,
        description="List of line item rows extracted from tables."
    )

# ──────────────────────────────────────────────────────────────────────
# Extraction Service Methods
# ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an expert Intelligent Document Processing (IDP) agent.\n"
    "Your task is to extract structured data from the document text provided by the user, based on their extraction instructions.\n\n"
    "You must structure your response containing two sections:\n"
    "1. 'headers': Key-value fields representing single metadata points (e.g. Invoice Number, Date, Vendor Name).\n"
    "2. 'line_items': Nested rows/columns representing tabular data (e.g. items on an invoice, descriptions, amounts).\n\n"
    "For EVERY extracted header and line item field, you must provide:\n"
    "- 'value': the extracted string value (or null if not found)\n"
    "- 'confidence': a confidence score (float between 0.0 and 1.0)\n"
    "- 'reasoning': a brief trace, evidence snippet, or explanation from the document text.\n\n"
    "Conform strictly to the StructuredExtractionResult schema. Return ONLY valid JSON."
)

# Compact prompt: asks for FLAT values only (no per-field confidence/reasoning). The verbose
# schema above forces value+confidence+reasoning for every one of N columns on every row, which
# overflows the model's output token limit on multi-row invoices → truncated/invalid JSON →
# dropped line items. The compact output is ~half the tokens; we re-attach a uniform default
# confidence in post-processing (`_expand_extraction`).
COMPACT_SYSTEM_PROMPT = (
    "You are an expert document data extractor.\n"
    "Extract the requested fields from the document text and return ONLY a JSON object of this shape:\n"
    '{"headers": {"field_name": "value", ...}, "line_items": [{"column_name": "value", ...}, ...]}\n\n'
    "Rules:\n"
    "- 'headers' are single document-level fields (invoice number, date, vendor, totals).\n"
    "- 'line_items' is a list of rows; each row is a flat object of column_name -> value.\n"
    "- Include ONLY fields/columns you actually find. Omit anything not present (do NOT invent values).\n"
    "- Dates as YYYY-MM-DD; numbers as plain strings. Values must be strings or null.\n"
    "- Do NOT include confidence or reasoning. Return ONLY valid JSON, no prose, no code fences."
)

# Uniform confidence assigned to any field the model returns in compact mode (it returns no
# real per-field score). Fields that are absent/empty get 0.0, so a document missing fields has
# a lower overall average and routes to HITL review.
_DEFAULT_FIELD_CONFIDENCE = 0.85


def _strip_code_fences(text: str) -> str:
    """Strip a leading/trailing ``` ... ``` markdown fence if present."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _expand_value(v: Any) -> tuple:
    """Map a scalar value OR a {value,confidence,reasoning} dict to (value:str|None, confidence, reasoning).

    Compact responses give a scalar → assign the uniform default confidence. A verbose response
    (dict with a real confidence) is preserved, so this works for both output styles.
    """
    if isinstance(v, dict):
        val = v.get("value")
        val_s = str(val) if val not in (None, "") else None
        if v.get("confidence") is not None:
            try:
                conf = float(v.get("confidence"))
            except (TypeError, ValueError):
                conf = _DEFAULT_FIELD_CONFIDENCE if val_s is not None else 0.0
        else:
            conf = _DEFAULT_FIELD_CONFIDENCE if val_s is not None else 0.0
        reasoning = v.get("reasoning") if v.get("reasoning") is not None else None
        return val_s, conf, reasoning
    val_s = str(v) if v not in (None, "") else None
    conf = _DEFAULT_FIELD_CONFIDENCE if val_s is not None else 0.0
    return val_s, conf, ("compact extraction" if val_s is not None else None)


def _expand_extraction(parsed: Any) -> Dict[str, Any]:
    """Expand a parsed LLM result (flat compact OR nested verbose) into the canonical shape that
    ``save_extraction_results`` consumes: ``{headers:{name:{value,confidence,reasoning}}, line_items:[...]}``."""
    if not isinstance(parsed, dict):
        return {"headers": {}, "line_items": []}

    headers: Dict[str, Any] = {}
    raw_headers = parsed.get("headers") or {}
    if isinstance(raw_headers, dict):
        for name, v in raw_headers.items():
            val, conf, reasoning = _expand_value(v)
            headers[str(name)] = {"value": val, "confidence": conf, "reasoning": reasoning}

    line_items: List[Dict[str, Any]] = []
    raw_rows = parsed.get("line_items") or []
    if isinstance(raw_rows, list):
        counter = 0
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            cols_out: List[Dict[str, Any]] = []
            cols = row.get("columns")
            if isinstance(cols, list):  # nested/verbose row
                for col in cols:
                    if not isinstance(col, dict):
                        continue
                    cname = str(col.get("column_name", "")).strip()
                    if not cname:
                        continue
                    src = col if col.get("confidence") is not None else col.get("value")
                    val, conf, reasoning = _expand_value(src)
                    cols_out.append({"column_name": cname, "value": val, "confidence": conf, "reasoning": reasoning})
            else:  # flat row: {column_name: value}
                for key, val_raw in row.items():
                    if key in ("row_index", "columns"):
                        continue
                    val, conf, reasoning = _expand_value(val_raw)
                    cols_out.append({"column_name": str(key), "value": val, "confidence": conf, "reasoning": reasoning})
            if cols_out:
                line_items.append({"row_index": counter, "columns": cols_out})
                counter += 1

    return {"headers": headers, "line_items": line_items}

async def extract_dynamic(
    ocr_text: str,
    prompt: str,
    llm_model: Any,
    compact: bool = True,
) -> Dict[str, Any]:
    """Extract structured data dynamically based on a freeform user prompt.

    ``compact=True`` (default) asks the model for a FLAT key-value JSON (no per-field
    confidence/reasoning) and re-attaches a uniform default confidence in post-processing —
    this avoids the output-token truncation of the verbose schema on multi-row invoices.
    ``compact=False`` keeps the verbose structured-output path (for tests asserting real
    per-field confidence). Both return the same canonical dict shape.
    """
    if llm_model is None:
        raise ValueError("Language Model is required for extraction.")

    user_content = f"Instruction: {prompt}\n\nDocument Text:\n{ocr_text}"

    # ── Compact path (default): flat JSON -> expand with default confidence ──
    if compact:
        messages = [
            SystemMessage(content=COMPACT_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]
        try:
            response = await llm_model.ainvoke(messages)
            raw_content = response.content if hasattr(response, "content") else str(response)
            parsed = json.loads(_strip_code_fences(raw_content))
            return _expand_extraction(parsed)
        except Exception as e:
            logger.error(f"[Extraction] compact extraction parsing failed: {e}")
            return {"headers": {}, "line_items": [], "error": f"Extraction parsing failed: {str(e)}"}

    # ── Verbose path (compact=False): structured schema ──
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_content)
    ]

    # Attempt structured output (tool-calling capable models)
    if hasattr(llm_model, "with_structured_output"):
        try:
            structured_model = llm_model.with_structured_output(StructuredExtractionResult)
            result = await structured_model.ainvoke(messages)
            if isinstance(result, StructuredExtractionResult):
                return result.model_dump()
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning(f"[LLM] with_structured_output failed: {e}. Falling back to raw JSON parsing.")

    # Fallback: raw JSON parsing
    response = await llm_model.ainvoke(messages)
    raw_content = response.content if hasattr(response, "content") else str(response)

    raw_content = raw_content.strip()
    if raw_content.startswith("```"):
        lines_raw = raw_content.split("\n")
        if lines_raw[0].strip().startswith("```"):
            lines_raw = lines_raw[1:]
        if lines_raw and lines_raw[-1].strip() == "```":
            lines_raw = lines_raw[:-1]
        raw_content = "\n".join(lines_raw).strip()

    parsed = json.loads(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError("Parsed LLM output is not a JSON object.")

    parsed.setdefault("headers", {})
    parsed.setdefault("line_items", [])

    clean_headers: Dict[str, Any] = {}
    for k, v in parsed["headers"].items():
        if isinstance(v, dict):
            clean_headers[k] = {
                "value": str(v["value"]) if v.get("value") is not None else None,
                "confidence": float(v.get("confidence", 0.8)),
                "reasoning": str(v["reasoning"]) if v.get("reasoning") is not None else None,
            }
        else:
            clean_headers[k] = {"value": str(v) if v is not None else None, "confidence": 0.8, "reasoning": "Direct extraction"}

    clean_line_items: List[Dict[str, Any]] = []
    for idx, row in enumerate(parsed["line_items"]):
        if not isinstance(row, dict):
            continue
        row_idx = row.get("row_index", idx)
        clean_cols = []
        for col in row.get("columns", []):
            if not isinstance(col, dict):
                continue
            clean_cols.append({
                "column_name": str(col.get("column_name", "")),
                "value": str(col["value"]) if col.get("value") is not None else None,
                "confidence": float(col.get("confidence", 0.8)),
                "reasoning": str(col["reasoning"]) if col.get("reasoning") is not None else None,
            })
        clean_line_items.append({"row_index": int(row_idx), "columns": clean_cols})

    return {"headers": clean_headers, "line_items": clean_line_items}


async def _compact_invoke(system: str, user: str, llm_model: Any) -> Dict[str, Any]:
    """Invoke the LLM with a compact (flat-JSON) system+user prompt and expand to the canonical shape.

    Plain ``ainvoke`` (NOT ``with_structured_output``): the verbose per-cell schema overflows the
    output-token budget on multi-row tables and drops ``line_items``. The model returns flat
    ``{"headers": {...}, "line_items": [{...}]}``; ``_expand_extraction`` re-attaches a uniform
    default confidence. On unparseable JSON, return an empty result + error (the pipeline treats a
    zero-field/error extraction as fatal/partial, matching ``extract_dynamic``'s compact path).
    """
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    try:
        response = await llm_model.ainvoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)
        parsed = json.loads(_strip_code_fences(raw_content))
        return _expand_extraction(parsed)
    except Exception as e:
        logger.error(f"[Extraction] compact named-config parsing failed: {e}")
        return {"headers": {}, "line_items": [], "error": f"Extraction parsing failed: {str(e)}"}


async def extract_named_config(
    session: AsyncSession,
    ocr_text: str,
    field_config_id: UUID,
    llm_model: Any
) -> Dict[str, Any]:
    """Extract structured data from a document conforming to a saved Field Configuration schema."""
    # 1. Fetch Configuration
    config = await session.get(IdpFieldConfiguration, field_config_id)
    if not config or config.deleted_at is not None:
        raise ValueError(f"Active field configuration '{field_config_id}' not found.")

    # 2. Fetch associated headers and line item fields
    headers_stmt = (
        select(IdpFieldConfigHeader)
        .where(IdpFieldConfigHeader.config_id == field_config_id)
        .order_by(IdpFieldConfigHeader.display_order)
    )
    headers = (await session.exec(headers_stmt)).all()

    lines_stmt = (
        select(IdpFieldConfigLineItem)
        .where(IdpFieldConfigLineItem.config_id == field_config_id)
        .order_by(IdpFieldConfigLineItem.display_order)
    )
    line_items = (await session.exec(lines_stmt)).all()

    # 3. Build a COMPACT prompt (flat values, field names + DB prompts as hints). The verbose
    #    per-cell value+confidence+reasoning schema truncates line_items on multi-row tables.
    system_prompt, user_prompt = build_compact_extraction_messages(headers, line_items, ocr_text)

    # 4. Invoke the LLM with plain ainvoke; _expand_extraction re-attaches uniform confidence.
    raw_result = await _compact_invoke(system_prompt, user_prompt, llm_model)

    # 5. Filter/Align the output to strictly match the requested configuration fields
    # (Removes hallucinations and maps missing properties with nulls/defaults)
    allowed_header_names = {h.field_name for h in headers}
    filtered_headers = {}
    for h_name in allowed_header_names:
        if h_name in raw_result.get("headers", {}):
            filtered_headers[h_name] = raw_result["headers"][h_name]
        else:
            filtered_headers[h_name] = {
                "value": None,
                "confidence": 0.0,
                "reasoning": "Not found in document"
            }

    allowed_column_names = {c.column_name for c in line_items}
    filtered_line_items = []
    for row in raw_result.get("line_items", []):
        row_idx = row.get("row_index", 0)
        clean_cols = []
        # Filter existing columns
        for col in row.get("columns", []):
            if col.get("column_name") in allowed_column_names:
                clean_cols.append(col)

        # Append missing columns
        existing_cols = {c["column_name"] for c in clean_cols}
        for col_name in allowed_column_names:
            if col_name not in existing_cols:
                clean_cols.append({
                    "column_name": col_name,
                    "value": None,
                    "confidence": 0.0,
                    "reasoning": "Not found in table"
                })

        filtered_line_items.append({
            "row_index": row_idx,
            "columns": clean_cols
        })

    return {
        "headers": filtered_headers,
        "line_items": filtered_line_items
    }


async def extract_multimodal(
    file_path: str | Path,
    prompt_or_config_id: str | UUID,
    llm_model: Any,
    session: Optional[AsyncSession] = None
) -> Dict[str, Any]:
    """Extract structured data from raw document pages directly using a Vision LLM without prior OCR."""
    if llm_model is None:
        raise ValueError("Language Model is required for multimodal extraction.")

    # 1. Convert file to page images (PNG bytes)
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    page_images = []
    if suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
        mime, _ = mimetypes.guess_type(str(file_path))
        mime = mime or "image/png"
        page_images.append((file_path.read_bytes(), mime))
    elif suffix == ".pdf":
        import fitz
        pdf_doc = fitz.open(str(file_path))
        for page_num in range(len(pdf_doc)):
            page = pdf_doc[page_num]
            zoom = 150 / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            page_images.append((pix.tobytes("png"), "image/png"))
        pdf_doc.close()
    else:
        raise ValueError(f"Unsupported file type for multimodal extraction: {suffix}")

    if not page_images:
        raise ValueError("No images found/rendered from the document.")

    # 2. Determine prompt and schema based on mode (dynamic prompt vs named configuration)
    is_config = isinstance(prompt_or_config_id, UUID) or (
        isinstance(prompt_or_config_id, str) and len(prompt_or_config_id) == 36 and "-" in prompt_or_config_id
    )

    headers = []
    line_items = []
    if is_config:
        if session is None:
            raise ValueError("AsyncSession is required for configuration-based multimodal extraction.")
        
        config_id = UUID(str(prompt_or_config_id))
        config = await session.get(IdpFieldConfiguration, config_id)
        if not config or config.deleted_at is not None:
            raise ValueError(f"Active field configuration '{config_id}' not found.")

        # Fetch associated headers and line item fields
        headers_stmt = (
            select(IdpFieldConfigHeader)
            .where(IdpFieldConfigHeader.config_id == config_id)
            .order_by(IdpFieldConfigHeader.display_order)
        )
        headers = (await session.exec(headers_stmt)).all()

        lines_stmt = (
            select(IdpFieldConfigLineItem)
            .where(IdpFieldConfigLineItem.config_id == config_id)
            .order_by(IdpFieldConfigLineItem.display_order)
        )
        line_items = (await session.exec(lines_stmt)).all()

        # Build prompt from general template; images are the document so pass a placeholder for {data}
        _sys, prompt = build_extraction_messages(
            headers, line_items, ocr_text="(Document content provided as image(s) below)"
        )
    else:
        _sys = SYSTEM_PROMPT
        prompt = str(prompt_or_config_id).strip()
        if not prompt:
            prompt = "Extract all key fields from this document. Return as structured JSON."

    # 3. Construct multimodal message payload (text instruction + base64 images)
    user_content: List[Any] = [{"type": "text", "text": prompt}]
    for img_bytes, mime in page_images:
        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        user_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_data}"}})

    messages = [SystemMessage(content=_sys), HumanMessage(content=user_content)]

    # 4. Invoke the Vision LLM model (structured output → raw JSON fallback)
    raw_result = None
    if hasattr(llm_model, "with_structured_output"):
        try:
            structured_model = llm_model.with_structured_output(StructuredExtractionResult)
            result = await structured_model.ainvoke(messages)
            if isinstance(result, StructuredExtractionResult):
                raw_result = result.model_dump()
            elif isinstance(result, dict):
                raw_result = result
        except Exception as e:
            logger.warning(f"[Multimodal] with_structured_output failed: {e}. Falling back to raw JSON.")

    if raw_result is None:
        try:
            response = await llm_model.ainvoke(messages)
            raw_text = response.content if hasattr(response, "content") else str(response)
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                lines_mm = raw_text.split("\n")
                if lines_mm[0].strip().startswith("```"):
                    lines_mm = lines_mm[1:]
                if lines_mm and lines_mm[-1].strip() == "```":
                    lines_mm = lines_mm[:-1]
                raw_text = "\n".join(lines_mm).strip()
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed output is not a JSON object.")
            parsed.setdefault("headers", {})
            parsed.setdefault("line_items", [])
            # Normalise the verbose multimodal response into the canonical shape.
            clean_h: Dict[str, Any] = {}
            for k, v in parsed["headers"].items():
                if isinstance(v, dict):
                    clean_h[k] = {
                        "value": str(v["value"]) if v.get("value") is not None else None,
                        "confidence": float(v.get("confidence", 0.8)),
                        "reasoning": str(v["reasoning"]) if v.get("reasoning") is not None else None,
                    }
                else:
                    clean_h[k] = {"value": str(v) if v is not None else None, "confidence": 0.8, "reasoning": "Direct extraction"}
            clean_li: List[Dict[str, Any]] = []
            for idx, row in enumerate(parsed["line_items"]):
                if not isinstance(row, dict):
                    continue
                clean_li.append({"row_index": row.get("row_index", idx), "columns": [
                    {"column_name": str(c.get("column_name", "")),
                     "value": str(c["value"]) if c.get("value") is not None else None,
                     "confidence": float(c.get("confidence", 0.8)),
                     "reasoning": str(c["reasoning"]) if c.get("reasoning") is not None else None}
                    for c in row.get("columns", []) if isinstance(c, dict)
                ]})
            raw_result = {"headers": clean_h, "line_items": clean_li}
        except Exception as e:
            logger.error(f"[Multimodal] parsing failed: {e}")
            raw_result = {"headers": {}, "line_items": [], "error": f"Multimodal extraction failed: {str(e)}"}

    # 5. Filter/Align if configuration mode was used
    if is_config:
        allowed_header_names = {h.field_name for h in headers}
        filtered_headers = {}
        for h_name in allowed_header_names:
            if h_name in raw_result.get("headers", {}):
                filtered_headers[h_name] = raw_result["headers"][h_name]
            else:
                filtered_headers[h_name] = {
                    "value": None,
                    "confidence": 0.0,
                    "reasoning": "Not found in document"
                }

        allowed_column_names = {c.column_name for c in line_items}
        filtered_line_items = []
        for row in raw_result.get("line_items", []):
            row_idx = row.get("row_index", 0)
            clean_cols = []
            for col in row.get("columns", []):
                if col.get("column_name") in allowed_column_names:
                    clean_cols.append(col)

            existing_cols = {c["column_name"] for c in clean_cols}
            for col_name in allowed_column_names:
                if col_name not in existing_cols:
                    clean_cols.append({
                        "column_name": col_name,
                        "value": None,
                        "confidence": 0.0,
                        "reasoning": "Not found in table"
                    })

            filtered_line_items.append({
                "row_index": row_idx,
                "columns": clean_cols
            })

        return {
            "headers": filtered_headers,
            "line_items": filtered_line_items
        }

    return raw_result


def _normalize_line_items(rows: Any) -> List[Dict[str, Any]]:
    """Normalize line items into the canonical ``{row_index, columns:[{column_name, value, ...}]}``.

    LLMs (esp. smaller/open models) sometimes return a FLAT row shape
    (``{"Item": "Widget", "Qty": "10"}``) instead of the nested ``columns`` shape.
    Both are accepted here so line items persist regardless of the model's output style.
    """
    normalized: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return normalized
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        row_idx = row.get("row_index", i)
        cols = row.get("columns")
        if isinstance(cols, list):
            # already canonical
            normalized.append({"row_index": row_idx, "columns": cols})
            continue
        # flat row -> wrap each key/value pair as a column
        flat_cols: List[Dict[str, Any]] = []
        for key, val in row.items():
            if key in ("row_index", "columns"):
                continue
            if isinstance(val, dict):
                flat_cols.append({
                    "column_name": key,
                    "value": val.get("value"),
                    "confidence": val.get("confidence", 0.0),
                    "reasoning": val.get("reasoning"),
                })
            else:
                flat_cols.append({"column_name": key, "value": val, "confidence": 0.0})
        if flat_cols:
            normalized.append({"row_index": row_idx, "columns": flat_cols})
    return normalized


async def save_extraction_results(
    session: AsyncSession,
    document_id: UUID,
    job_id: UUID,
    extraction_result: Dict[str, Any],
    ocr_tokens: Optional[List[Dict[str, Any]]] = None
) -> float:
    """Save the structured extraction results to the database and compute/update confidence.
    
    Persists data to idp_extracted_headers and idp_extracted_line_items,
    maps source locations (bounding boxes) using OCR tokens, and
    calculates/updates the overall document confidence.
    """
    from sqlalchemy import delete
    from agentcore.services.database.models.idp.documents import (
        IdpExtractedHeader,
        IdpExtractedLineItem,
        IdpDocument
    )

    # 1. Clean up existing extraction records for this job to ensure idempotency
    await session.execute(delete(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id))
    await session.execute(delete(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job_id))

    confidences = []
    
    # 2. Save headers
    headers_dict = extraction_result.get("headers", {})
    for field_name, field_data in headers_dict.items():
        val = field_data.get("value")
        extracted_val = str(val) if val is not None else None
        conf = float(field_data.get("confidence", 0.0))
        reasoning = field_data.get("reasoning")
        
        # Resolve source location using ocr_tokens matching
        source_loc = None
        if ocr_tokens and extracted_val:
            val_lower = extracted_val.strip().lower()
            for token in ocr_tokens:
                tok_text = str(token.get("text", "")).strip().lower()
                if val_lower in tok_text or tok_text in val_lower:
                    source_loc = {
                        "page_number": token.get("page_number", 1),
                        "bounding_box": token.get("bounding_box"),
                        "confidence": token.get("confidence", 1.0)
                    }
                    break

        header_rec = IdpExtractedHeader(
            id=uuid4(),
            document_id=document_id,
            job_id=job_id,
            field_name=field_name,
            extracted_value=extracted_val,
            confidence_score=conf,
            reasoning_trace=reasoning,
            source_location=source_loc,
            is_reviewed=False
        )
        session.add(header_rec)
        confidences.append(conf)

    # 3. Save line items (normalized so flat/nested LLM shapes both persist)
    line_items_list = _normalize_line_items(extraction_result.get("line_items", []))
    for row in line_items_list:
        row_idx = row.get("row_index", 0)
        cols = row.get("columns", [])
        for col in cols:
            col_name = col.get("column_name")
            val = col.get("value")
            extracted_val = str(val) if val is not None else None
            conf = float(col.get("confidence", 0.0))
            reasoning = col.get("reasoning")
            
            # Resolve source location
            source_loc = None
            if ocr_tokens and extracted_val:
                val_lower = extracted_val.strip().lower()
                for token in ocr_tokens:
                    tok_text = str(token.get("text", "")).strip().lower()
                    if val_lower in tok_text or tok_text in val_lower:
                        source_loc = {
                            "page_number": token.get("page_number", 1),
                            "bounding_box": token.get("bounding_box"),
                            "confidence": token.get("confidence", 1.0)
                        }
                        break

            line_rec = IdpExtractedLineItem(
                id=uuid4(),
                document_id=document_id,
                job_id=job_id,
                row_index=row_idx,
                column_name=col_name,
                extracted_value=extracted_val,
                confidence_score=conf,
                reasoning_trace=reasoning,
                source_location=source_loc,
                is_reviewed=False
            )
            session.add(line_rec)
            confidences.append(conf)

    # 4. Calculate overall weighted average confidence.
    # No fields extracted => zero confidence (route to human review), NOT 1.0.
    overall_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # 5. Update IdpDocument overall confidence
    doc = await session.get(IdpDocument, document_id)
    if doc:
        doc.overall_confidence = overall_conf
        session.add(doc)

    await session.commit()
    return overall_conf
