import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage

_LLM_TIMEOUT = 120  # seconds — fail fast rather than hang forever
# Cap the number of page images sent to a vision model in ONE extraction request (Codex #8: an unbounded
# scan would blow the provider's request-size limit and fail the whole doc). Configurable; a Page
# Selector upstream is the precise control over which pages are read.
try:
    _MAX_VISION_PAGES = max(1, int(os.getenv("IDP_MAX_VISION_PAGES", "20") or "20"))
except (TypeError, ValueError):
    _MAX_VISION_PAGES = 20
from sqlmodel.ext.asyncio.session import AsyncSession
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

# Compact prompt: asks for {value, confidence} per field — no reasoning text.
# Reasoning was the token-overflow culprit on multi-row invoices (each reasoning string
# could be 100-200 chars × N fields × M rows = output limit exceeded → truncated JSON →
# dropped line items). Dropping reasoning and keeping only a float per field adds ~12 tokens
# per field — negligible — while giving real model-generated confidence instead of a flat 0.85.
COMPACT_SYSTEM_PROMPT = (
    "You are an expert document data extractor.\n"
    "Extract the requested fields from the document text and return ONLY a valid JSON object.\n\n"
    "Output shape:\n"
    '{"headers": {"field_name": {"value": "extracted string or null", "confidence": 0.95}, ...},\n'
    ' "line_items": [{"col_name": {"value": "extracted string or null", "confidence": 0.87}, ...}, ...]}\n\n'
    "Rules:\n"
    "- 'headers': single document-level fields (invoice number, date, vendor, totals, etc.).\n"
    "- 'line_items': list of rows; each row is a flat object of column_name -> {value, confidence}.\n"
    "- 'confidence' MUST be a genuine float 0.0–1.0 reflecting extraction certainty. "
    "Use the full range — NOT binary:\n"
    "    0.90–1.00 = value stated verbatim and unambiguous in the document\n"
    "    0.70–0.89 = clearly present but requires minor inference (e.g. format conversion)\n"
    "    0.40–0.69 = partially present, ambiguous, or derived from context\n"
    "    0.00–0.39 = absent, guessed, or very uncertain\n"
    "- If a field is not present in the document: return null value with confidence 0.0.\n"
    "- Dates as YYYY-MM-DD; numbers as plain strings without currency symbols. Values must be strings or null.\n"
    "- Do NOT include reasoning text. Return ONLY valid JSON, no prose, no markdown fences."
)

def _model_confidence(raw: Any, value: Any) -> float | None:
    """The MODEL's own confidence for one field, clamped to [0,1]. Never a made-up constant.

    * ``0.0``  — the field has no value. Stored for the UI, excluded from the overall mean.
    * ``None`` — the field HAS a value but the model reported no usable confidence. We do not invent one:
      an unknown confidence is not a confident one. It is stored as SQL NULL and excluded from the mean,
      so a response where the model omits every confidence yields ``overall=0.0`` and routes to review.
    * otherwise the model's float.

    Every populated field used to get a hardcoded ``0.75`` here (and ``0.8`` on the structured-output path),
    which then got overwritten downstream by an OCR-substring score — so the model's judgement never reached
    the database at all. Out-of-range values are clamped rather than rescaled: a model that answers ``95``
    meaning 95% is indistinguishable from one that is badly broken, and clamping to 1.0 fails safe toward
    "the model claims certainty" rather than silently inventing ``0.95``.
    """
    if value is None or not str(value).strip():
        return 0.0
    if raw is None:
        return None
    try:
        conf = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, conf))


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


def _loads_lenient(raw_text: str) -> Any:
    """Parse a model's JSON output, tolerating the usual LLM malformations.

    Vision / text models frequently emit *almost*-valid JSON — a trailing comma before ``}``/``]``,
    an unquoted key, prose around the object, or a truncated tail when they hit the output-token
    limit. A plain ``json.loads`` raises ``JSONDecodeError`` on any of these and fails the whole
    extraction. This strips code fences, tries a strict parse, and on failure repairs with
    ``json_repair`` (closes open braces, drops trailing commas, quotes keys). Re-raises the original
    error only if the output is truly unrecoverable.
    """
    text = _strip_code_fences(raw_text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, (dict, list)):
                logger.warning("[Extraction] model JSON was malformed — repaired with json_repair")
                return repaired
        except Exception:
            pass
        raise


def _expand_value(v: Any) -> tuple:
    """Map a ``{value,confidence,reasoning}`` dict OR a bare scalar to ``(value:str|None, confidence, reasoning)``.

    Both compact and verbose prompts now request the dict form. A bare scalar means the model ignored the
    schema; it yields ``confidence=None`` (unknown), and the caller logs it. It used to yield a hardcoded
    0.75 with ``reasoning="compact extraction"`` — which is why every field in the database carries that
    string and why "the LLM's confidence" was a constant nobody had ever measured.
    """
    if isinstance(v, dict):
        val = v.get("value")
        val_s = str(val) if val not in (None, "") else None
        reasoning = v.get("reasoning") if v.get("reasoning") is not None else None
        return val_s, _model_confidence(v.get("confidence"), val_s), reasoning
    val_s = str(v) if v not in (None, "") else None
    return val_s, _model_confidence(None, val_s), None


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

def _extract_usage_from_response(response: Any, model_fallback: str | None = None) -> Dict[str, Any]:
    """Helper to extract token usage and model name from a Langchain response object."""
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": model_fallback,
    }
    if not response:
        return usage

    # 1. Try usage_metadata (standard in modern Langchain)
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        um = response.usage_metadata
        usage["input_tokens"] = um.get("input_tokens") or um.get("prompt_tokens") or 0
        usage["output_tokens"] = um.get("output_tokens") or um.get("completion_tokens") or 0
        usage["total_tokens"] = um.get("total_tokens") or 0

    # 2. Try response_metadata (provider-specific fields)
    elif hasattr(response, "response_metadata") and response.response_metadata:
        rm = response.response_metadata
        tu = rm.get("token_usage")
        if isinstance(tu, dict):
            usage["input_tokens"] = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            usage["output_tokens"] = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            usage["total_tokens"] = tu.get("total_tokens") or 0
        else:
            usage["input_tokens"] = rm.get("prompt_tokens") or rm.get("input_tokens") or 0
            usage["output_tokens"] = rm.get("completion_tokens") or rm.get("output_tokens") or 0
            usage["total_tokens"] = rm.get("total_tokens") or 0

    if usage["total_tokens"] == 0:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

    # Extract model name
    if hasattr(response, "response_metadata") and isinstance(response.response_metadata, dict):
        model_name = response.response_metadata.get("model_name") or response.response_metadata.get("model")
        if model_name:
            usage["model"] = str(model_name)

    if not usage["model"] and hasattr(response, "model"):
        usage["model"] = str(response.model)

    return usage

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
            response = await asyncio.wait_for(llm_model.ainvoke(messages), timeout=_LLM_TIMEOUT)
            raw_content = response.content if hasattr(response, "content") else str(response)
            parsed = _loads_lenient(raw_content)
            res = _expand_extraction(parsed)
            res["_usage"] = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))
            return res
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
            try:
                structured_model = llm_model.with_structured_output(StructuredExtractionResult, include_raw=True)
                res_dict = await asyncio.wait_for(structured_model.ainvoke(messages), timeout=_LLM_TIMEOUT)
                if isinstance(res_dict, dict):
                    result = res_dict.get("parsed")
                    raw_response = res_dict.get("raw")
                else:
                    result = res_dict
                    raw_response = None
            except TypeError:
                structured_model = llm_model.with_structured_output(StructuredExtractionResult)
                result = await asyncio.wait_for(structured_model.ainvoke(messages), timeout=_LLM_TIMEOUT)
                raw_response = None

            if isinstance(result, StructuredExtractionResult):
                if not result.headers and not result.line_items:
                    raise ValueError("Structured output returned empty headers and line items")
                res = result.model_dump()
                res["_usage"] = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
                return res
            if isinstance(result, dict):
                if not result.get("headers") and not result.get("line_items"):
                    raise ValueError("Structured output returned empty headers and line items")
                res = dict(result)
                res["_usage"] = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
                return res
        except Exception as e:
            logger.warning(f"[LLM] with_structured_output failed: {e}. Falling back to raw JSON parsing.")

    # Fallback: raw JSON parsing
    response = await asyncio.wait_for(llm_model.ainvoke(messages), timeout=_LLM_TIMEOUT)
    raw_content = response.content if hasattr(response, "content") else str(response)

    raw_content = raw_content.strip()
    if raw_content.startswith("```"):
        lines_raw = raw_content.split("\n")
        if lines_raw[0].strip().startswith("```"):
            lines_raw = lines_raw[1:]
        if lines_raw and lines_raw[-1].strip() == "```":
            lines_raw = lines_raw[:-1]
        raw_content = "\n".join(lines_raw).strip()

    parsed = _loads_lenient(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError("Parsed LLM output is not a JSON object.")

    parsed.setdefault("headers", {})
    parsed.setdefault("line_items", [])

    clean_headers: Dict[str, Any] = {}
    for k, v in parsed["headers"].items():
        if isinstance(v, dict):
            _val = str(v["value"]) if v.get("value") is not None else None
            clean_headers[k] = {
                "value": _val,
                "confidence": _model_confidence(v.get("confidence"), _val),
                "reasoning": str(v["reasoning"]) if v.get("reasoning") is not None else None,
            }
        else:
            _val = str(v) if v is not None else None
            clean_headers[k] = {"value": _val, "confidence": _model_confidence(None, _val), "reasoning": None}

    clean_line_items: List[Dict[str, Any]] = []
    for idx, row in enumerate(parsed["line_items"]):
        if not isinstance(row, dict):
            continue
        row_idx = row.get("row_index", idx)
        clean_cols = []
        for col in row.get("columns", []):
            if not isinstance(col, dict):
                continue
            _val = str(col["value"]) if col.get("value") is not None else None
            clean_cols.append({
                "column_name": str(col.get("column_name", "")),
                "value": _val,
                "confidence": _model_confidence(col.get("confidence"), _val),
                "reasoning": str(col["reasoning"]) if col.get("reasoning") is not None else None,
            })
        clean_line_items.append({"row_index": int(row_idx), "columns": clean_cols})

    res = {"headers": clean_headers, "line_items": clean_line_items}
    res["_usage"] = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))
    return res


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
        parsed = _loads_lenient(raw_content)
        res = _expand_extraction(parsed)
        res["_usage"] = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))
        return res
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
    # 1+2. Field definitions: this run's FROZEN copy on a published run (the config is mutable in place
    #      and the graph only names it), else the live tables. Raises if neither is available.
    from agentcore.services.idp.field_defs import load_field_definitions

    headers, line_items = await load_field_definitions(session, field_config_id)

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

    final_res = {
        "headers": filtered_headers,
        "line_items": filtered_line_items
    }
    if "_usage" in raw_result:
        final_res["_usage"] = raw_result["_usage"]
    return final_res


# ──────────────────────────────────────────────────────────────────────
# Dynamic vision (multimodal) extraction — decide route, render, extract
# ──────────────────────────────────────────────────────────────────────

def decide_extraction_input(
    input_mode: str | None,
    overall_kind: str,
    supports_vision: bool,
    has_ocr_node: bool,
) -> str:
    """Decide the extraction route BEFORE OCR runs, from the agent's Input Mode + the detected
    document kind + whether an OCR node is on the canvas + the model's vision capability.

    Returns one of:
      "text"        — run the existing text path (OCR if scanned, native text if digital)
      "vision"      — skip OCR entirely; render page images and send to a vision model
      "text_vision" — text path AND page images

    ``overall_kind`` ∈ {"digital","scanned","mixed"} (digital/mixed => a native text layer exists).
    ``supports_vision`` is checkbox-only: True only when the model is marked 'Supports vision'.
    ``has_ocr_node`` is True when a PaddleOCR node is present on the agent canvas.

    AUTO is canvas-driven: a native text layer wins; else on a scanned doc an OCR node wins
    (text/OCR) over vision; else a vision model routes to vision; else — a scanned doc with no OCR
    node and a non-vision model — raise ``PipelineError`` (nothing can read it). Explicit
    vision/text_vision on a non-vision model also raises.
    """
    # Lazy import avoids the extraction<->pipeline import cycle (pipeline imports extraction).
    from agentcore.services.idp.pipeline import PipelineError

    mode = (input_mode or "auto").strip().lower()
    has_native_text = overall_kind in ("digital", "mixed")

    if mode == "text":
        return "text"

    if mode in ("vision", "text_vision"):
        if not supports_vision:
            raise PipelineError(
                "This Input Mode needs a vision-capable model — open the model in the Model "
                "Catalogue and enable 'Supports vision'."
            )
        # text_vision needs native/OCR text to pair with the images; on a scanned doc with no
        # native text there is nothing to pair, so degrade to vision (pipeline logs a warn).
        if mode == "text_vision" and not has_native_text:
            return "vision"
        return mode

    # auto — canvas-driven
    if has_native_text:
        return "text"                 # digital/mixed: use the native text layer (no OCR/vision)
    if has_ocr_node:
        return "text"                 # scanned + a PaddleOCR node on the canvas -> run OCR (OCR wins)
    if supports_vision:
        return "vision"               # scanned + no OCR node + vision-capable model -> vision (no OCR)
    # scanned + no OCR node + non-vision model -> nothing can read the document
    raise PipelineError(
        "This document is scanned (image-only) but the agent has no OCR node and the selected "
        "model is not vision-capable. Add a PaddleOCR node, or pick a model marked 'Supports "
        "vision', to process scanned documents."
    )


def render_document_images(
    file_bytes: bytes,
    file_type: str,
    selected_pages: set[int] | None = None,
) -> List[tuple]:
    """Render a document (from raw bytes) to a list of ``(png_bytes, mime)`` for vision extraction.

    Images are passed through as-is; PDFs are rendered page-by-page at 150 DPI (mirrors
    ``extract_multimodal``). ``selected_pages`` is a 1-based set (from the Page Selector);
    ``None`` renders every page. Rendering from a byte stream (not a path) so it works with the
    in-memory ``original_bytes`` the pipeline already holds — no temp file needed.
    """
    if not file_bytes:
        raise ValueError("cannot render vision images: the document is empty (0 bytes)")

    ft = (file_type or "").lower().lstrip(".")
    _IMG_TYPES = ("png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp")
    if ft in _IMG_TYPES:
        # Validate the image actually decodes before base64-ing it to a model (a corrupt/empty
        # image would otherwise fail with an opaque provider 500). Skip the check only if Pillow
        # is unavailable, so a missing optional dep never rejects a valid image.
        try:
            from PIL import Image as _PILImage
            import io as _io
        except Exception:
            _PILImage = None
        if _PILImage is not None:
            try:
                with _PILImage.open(_io.BytesIO(file_bytes)) as _im:
                    _im.verify()
            except Exception as e:
                raise ValueError(
                    f"cannot render vision images: the .{ft} file is not a valid/decodable image ({e})"
                ) from e
        return [(file_bytes, mimetypes.types_map.get("." + ft, "image/png"))]

    if ft != "pdf":
        raise ValueError(
            f"cannot render vision images: file type '.{ft}' is not supported for vision "
            f"(supported: pdf, {', '.join(_IMG_TYPES)}). Use Text mode / an OCR node for this file, "
            f"or convert it to PDF."
        )

    import fitz
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"cannot render vision images: the PDF could not be opened ({e})") from e
    out: List[tuple] = []
    capped = False
    try:
        for i in range(len(doc)):
            if selected_pages is not None and (i + 1) not in selected_pages:
                continue
            if len(out) >= _MAX_VISION_PAGES:
                capped = True
                break
            out.append((doc[i].get_pixmap(dpi=150).tobytes("png"), "image/png"))
    finally:
        doc.close()
    if capped:
        logger.warning(
            f"vision extraction: document has more than {_MAX_VISION_PAGES} rendered page(s); only the "
            f"first {_MAX_VISION_PAGES} are sent to the model (avoids the request size limit). Add a Page "
            f"Selector to choose pages, or raise IDP_MAX_VISION_PAGES."
        )
    return out


async def extract_vision(
    page_images: List[tuple],
    *,
    llm_model: Any,
    prompt: str | None = None,
    field_config_messages: tuple | None = None,
    ocr_text: str | None = None,
) -> Dict[str, Any]:
    """Extract structured data from PRE-RENDERED page images using a vision LLM (no OCR).

    Exactly one instruction source drives the call:
      * ``field_config_messages`` — a ``(system, user)`` tuple from a Field Configuration
        (built by ``build_compact_extraction_messages_vision``), OR
      * ``prompt`` — a freeform dynamic-mode instruction.
    ``ocr_text`` (text_vision mode only) is appended to the leading text block so the model sees
    the OCR text alongside the images — this is the ONE place the OCR text is added.

    Returns the canonical shape ``save_extraction_results`` consumes:
    ``{headers:{name:{value,confidence,reasoning}}, line_items:[{row_index,columns:[...]}]}``.
    Mirrors ``extract_multimodal``'s invoke/normalize but takes pre-rendered images + a ready prompt
    (rendering + config lookup are done by the caller/pipeline).
    """
    if llm_model is None:
        raise ValueError("Language Model is required for vision extraction.")
    if not page_images:
        raise ValueError("No page images provided for vision extraction.")

    # 1. Instruction (system + leading text) — named-config messages OR a dynamic prompt.
    if field_config_messages is not None:
        sys_prompt, text_instruction = field_config_messages
    else:
        sys_prompt = SYSTEM_PROMPT
        text_instruction = (prompt or "").strip() or "Extract all key fields from this document. Return as structured JSON."

    if ocr_text and ocr_text.strip():
        text_instruction = (
            f"{text_instruction}\n\n"
            "### OCR text from the SAME document (use it together with the image(s)):\n"
            f"-------\n{ocr_text}\n-------"
        )

    # 2. Multimodal payload: leading text block, then one image_url block per page.
    user_content: List[Any] = [{"type": "text", "text": text_instruction}]
    for img_bytes, mime in page_images:
        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        user_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64_data}"}})

    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=user_content)]

    # 3. Invoke (structured output → raw-JSON fallback) and normalize to the canonical shape.
    raw_result: Optional[Dict[str, Any]] = None
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

            if isinstance(result, StructuredExtractionResult):
                if result.headers or result.line_items:
                    raw_result = result.model_dump()
                    usage_info = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
            elif isinstance(result, dict):
                if result.get("headers") or result.get("line_items"):
                    raw_result = result
                    usage_info = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
        except Exception as e:
            logger.warning(f"[Vision] with_structured_output failed: {e}. Falling back to raw JSON.")

    if raw_result is None:
        response = await llm_model.ainvoke(messages)
        raw_text = response.content if hasattr(response, "content") else str(response)
        raw_text = _strip_code_fences(raw_text or "")
        parsed = _loads_lenient(raw_text)  # tolerant of the model's near-valid JSON (repairs trailing commas / truncation)
        if not isinstance(parsed, dict):
            raise ValueError("Vision model output was not a JSON object.")
        raw_result = _expand_extraction(parsed)
        usage_info = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))

    if usage_info and isinstance(raw_result, dict):
        raw_result["_usage"] = usage_info
    return raw_result


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
        def _render_pdf_pages() -> list[tuple]:
            # CPU-bound rasterization — runs in a worker thread so the event loop stays responsive
            import fitz
            out: list[tuple] = []
            pdf_doc = fitz.open(str(file_path))
            try:
                for page_num in range(len(pdf_doc)):
                    page = pdf_doc[page_num]
                    zoom = 150 / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    out.append((pix.tobytes("png"), "image/png"))
            finally:
                pdf_doc.close()
            return out

        page_images = await asyncio.to_thread(_render_pdf_pages)
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
        # Frozen definitions on a published run, else the live tables (see services/idp/field_defs.py).
        from agentcore.services.idp.field_defs import load_field_definitions

        headers, line_items = await load_field_definitions(session, config_id)

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

            if isinstance(result, StructuredExtractionResult):
                if not result.headers and not result.line_items:
                    raise ValueError("Structured output returned empty headers and line items")
                raw_result = result.model_dump()
                usage_info = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
            elif isinstance(result, dict):
                if not result.get("headers") and not result.get("line_items"):
                    raise ValueError("Structured output returned empty headers and line items")
                raw_result = result
                usage_info = _extract_usage_from_response(raw_response, model_fallback=getattr(llm_model, "model_name", None))
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
            parsed = _loads_lenient(raw_text)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed output is not a JSON object.")
            parsed.setdefault("headers", {})
            parsed.setdefault("line_items", [])
            # Normalise the verbose multimodal response into the canonical shape. Confidence comes from the
            # model or it is None — a `0.8` default here would be the vision path's copy of the `0.75` bug.
            clean_h: Dict[str, Any] = {}
            for k, v in parsed["headers"].items():
                if isinstance(v, dict):
                    _hv = str(v["value"]) if v.get("value") is not None else None
                    clean_h[k] = {
                        "value": _hv,
                        "confidence": _model_confidence(v.get("confidence"), _hv),
                        "reasoning": str(v["reasoning"]) if v.get("reasoning") is not None else None,
                    }
                else:
                    _hv = str(v) if v is not None else None
                    clean_h[k] = {"value": _hv, "confidence": _model_confidence(None, _hv), "reasoning": None}
            clean_li: List[Dict[str, Any]] = []
            for idx, row in enumerate(parsed["line_items"]):
                if not isinstance(row, dict):
                    continue
                cols: List[Dict[str, Any]] = []
                for c in row.get("columns", []):
                    if not isinstance(c, dict):
                        continue
                    _cv = str(c["value"]) if c.get("value") is not None else None
                    cols.append({
                        "column_name": str(c.get("column_name", "")),
                        "value": _cv,
                        "confidence": _model_confidence(c.get("confidence"), _cv),
                        "reasoning": str(c["reasoning"]) if c.get("reasoning") is not None else None,
                    })
                clean_li.append({"row_index": row.get("row_index", idx), "columns": cols})
            raw_result = {"headers": clean_h, "line_items": clean_li}
            usage_info = _extract_usage_from_response(response, model_fallback=getattr(llm_model, "model_name", None))
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

        final_res = {
            "headers": filtered_headers,
            "line_items": filtered_line_items
        }
        if usage_info:
            final_res["_usage"] = usage_info
        return final_res

    if usage_info and isinstance(raw_result, dict):
        raw_result["_usage"] = usage_info
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
                    "confidence": _model_confidence(val.get("confidence"), val.get("value")),
                    "reasoning": val.get("reasoning"),
                })
            else:
                # A bare scalar cell: the model ignored the {value, confidence} schema. Unknown, not 0.0 —
                # a hard 0.0 would drag the document's mean down and route a good extraction to review.
                flat_cols.append({"column_name": key, "value": val, "confidence": _model_confidence(None, val)})
        if flat_cols:
            normalized.append({"row_index": row_idx, "columns": flat_cols})
    return normalized


def field_confidences(extraction_result: Dict[str, Any]) -> List[float]:
    """The MODEL'S OWN per-field confidence for every POPULATED header and line-item cell, in save order.

    THE single definition of "how confident are we". ``save_extraction_results`` persists these, and
    ``graph_native.payload.overall_confidence`` (the Confidence Router / Rules / Approval Gate) averages
    the same list — so the number that routes a document is the number stored on it and shown in the UI.

    Two exclusions, both deliberate:

    * **No value** — averaging absent fields in as 0.0 would drag the mean far below the real confidence of
      what WAS extracted. A 40-field config that legitimately finds 12 fields is not 30% confident.
    * **Confidence ``None``** — the model returned a value but no usable confidence (it ignored the schema).
      Unknown is not zero and it is not 0.75 either. If EVERY populated field is unknown this list is empty
      and the overall confidence is 0.0, which routes the document to review. That is the safe direction:
      we know nothing about it, so a human looks.

    Grounding is deliberately absent. Whether a value actually appears in the document is an INDEPENDENT
    signal (:class:`grounding.Grounder`); folding it into this number is precisely the bug this replaced.
    An OCR substring ratio was overwriting the model's judgement, so a value the model flagged as a guess
    scored 1.0 for appearing on the page, and a correctly reformatted date scored 0.285 for not appearing
    verbatim. Route on confidence AND grounding — never on their product.
    """
    confs: List[float] = []

    def _take(entry: Any) -> None:
        if not isinstance(entry, dict) or entry.get("value") is None:
            return
        conf = entry.get("confidence")
        if conf is None:
            return
        confs.append(float(conf))

    for field_data in (extraction_result.get("headers") or {}).values():
        _take(field_data)
    for row in _normalize_line_items(extraction_result.get("line_items", [])):
        for col in row.get("columns", []):
            _take(col)

    return confs


def compute_overall_confidence(extraction_result: Dict[str, Any]) -> float:
    """Mean of :func:`field_confidences`. Nothing usable -> 0.0 (routes to review, never auto-approves)."""
    confs = field_confidences(extraction_result)
    return sum(confs) / len(confs) if confs else 0.0


async def save_extraction_results(
    session: AsyncSession,
    document_id: UUID,
    job_id: UUID,
    extraction_result: Dict[str, Any],
    ocr_tokens: Optional[List[Dict[str, Any]]] = None,
    skip_if_already_saved: bool = False,
) -> float:
    """Persist extraction results. Returns the document's overall confidence.

    Two INDEPENDENT signals are stored per field, and they must not be mixed:

    * ``confidence_score`` — the model's own number (NULL if it reported none). Only this is averaged.
    * ``grounded`` / ``source_location`` — whether the value actually appears in ``ocr_tokens``, decided by
      :class:`grounding.Grounder`, which the model does not influence. ``grounded=None`` means there was no
      token stream to check against (vision path); it is *unknown*, not a failure.

    ``ocr_tokens`` therefore no longer affects the score at all — it only decides grounding. It used to BE
    the score: a substring ratio against the token stream overwrote whatever the model said.

    CONCURRENCY: a native graph can reach more than one ``Processed Docs Output`` (both branches of a
    Confidence Router are wired to one), and each sink runs in its OWN session. The delete-then-insert
    below is idempotent only when serialized — run it twice in parallel and both DELETEs see nothing,
    both INSERT, and the second dies on ``uq_idp_ext_header_job_field``. So take a row lock on the
    document first: it makes every writer for this document queue up behind the one in flight.

    ``skip_if_already_saved`` then makes an opportunistic writer **never downgrade** what is already there.
    The losing sink is the one on the router branch the document did not take: it carries no OCR tokens, so
    its rows would all have ``grounded=None``. Note the rule is NOT "first writer wins" — which sink reaches
    the lock first is arbitrary, so that would lose the grounding half the time. It is:

        * rows exist AND they were grounding-checked        -> keep them, save nothing
        * rows exist, unchecked, and WE have no tokens      -> keep them (nothing to add)
        * rows exist, unchecked, and WE have tokens         -> replace them (an upgrade)
        * no rows                                           -> save

    Callers that own the document's single save (``pipeline._run``) leave the flag False and get the old
    replace-everything behavior.
    """
    from sqlalchemy import delete, select as sa_select
    from agentcore.services.database.models.idp.documents import (
        IdpExtractedHeader,
        IdpExtractedLineItem,
        IdpDocument
    )

    # 0. Serialize every writer for this document. Concurrent sinks now queue instead of colliding.
    await session.execute(sa_select(IdpDocument.id).where(IdpDocument.id == document_id).with_for_update())

    from agentcore.services.idp.grounding import Grounder, evidence_trace, grounding_summary

    if skip_if_already_saved:
        # Read `grounded` in PYTHON, not via SQL COUNT. It is tri-state, and `COUNT(grounded)` would treat
        # an explicit `False` (the hallucination signal) identically to a `True`. Only a handful of rows.
        flags = (
            await session.execute(
                sa_select(IdpExtractedHeader.grounded).where(IdpExtractedHeader.job_id == job_id)
            )
        ).scalars().all()
        already = len(flags)
        checked = sum(1 for g in flags if g is not None)
        # Only overwrite to UPGRADE unchecked rows with grounding-checked ones.
        if already and (checked or not ocr_tokens):
            doc = await session.get(IdpDocument, document_id)
            existing_conf = float(doc.overall_confidence or 0.0) if doc else 0.0
            logger.info(
                f"[Extraction] {document_id}: job {job_id} already has {already} header(s) "
                f"({checked} grounding-checked) — another sink persisted this run; not overwriting "
                f"(conf={existing_conf:.2f})."
            )
            await session.commit()   # release the row lock
            return existing_conf
        if already:
            logger.info(
                f"[Extraction] {document_id}: replacing {already} unchecked header(s) for job {job_id} "
                f"with a grounding-checked save."
            )

    # 1. Delete stale records so re-processing is idempotent.
    await session.execute(delete(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id))
    await session.execute(delete(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job_id))

    # Built ONCE for the whole document — it normalizes and indexes the token stream. `_ocr_evidence`
    # re-joined every token into one string per field, i.e. 40 full passes for a 40-field config.
    grounder = Grounder(ocr_tokens)
    grounded_flags: List[Optional[bool]] = []
    ungrounded: List[str] = []
    missing_conf: List[str] = []

    # 2. Save header fields.
    headers_dict = extraction_result.get("headers", {})
    for field_name, field_data in headers_dict.items():
        val = field_data.get("value")
        extracted_val = str(val) if val is not None else None
        conf = field_data.get("confidence")          # the MODEL's number; None when it reported none

        source_loc, grounded = grounder.check(extracted_val)
        # Quote the document, don't ask the model to justify itself. Only the verbose/free-text prompt
        # supplies `reasoning`; the compact one is forbidden from doing so (output-token overflow).
        reasoning = field_data.get("reasoning") or evidence_trace(grounded, source_loc)
        if extracted_val is not None:
            grounded_flags.append(grounded)
            if grounded is False:
                ungrounded.append(field_name)
            if conf is None:
                missing_conf.append(field_name)

        header_rec = IdpExtractedHeader(
            id=uuid4(),
            document_id=document_id,
            job_id=job_id,
            field_name=field_name,
            extracted_value=extracted_val,
            confidence_score=conf,
            reasoning_trace=reasoning,
            source_location=source_loc,
            grounded=grounded,
            is_reviewed=False,
        )
        session.add(header_rec)

    # 3. Save line items (handles both flat and nested LLM output shapes).
    line_items_list = _normalize_line_items(extraction_result.get("line_items", []))
    for row in line_items_list:
        row_idx = row.get("row_index", 0)
        for col in row.get("columns", []):
            col_name = col.get("column_name")
            val = col.get("value")
            extracted_val = str(val) if val is not None else None
            conf = col.get("confidence")

            source_loc, grounded = grounder.check(extracted_val)
            reasoning = col.get("reasoning") or evidence_trace(grounded, source_loc)
            if extracted_val is not None:
                grounded_flags.append(grounded)
                if grounded is False:
                    ungrounded.append(f"row{row_idx}.{col_name}")
                if conf is None:
                    missing_conf.append(f"row{row_idx}.{col_name}")

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
                grounded=grounded,
                is_reviewed=False,
            )
            session.add(line_rec)

    # 4. Overall confidence = mean of the MODEL's per-field confidences. Computed by the SAME function the
    #    Confidence Router / Rules / Approval Gate use, so the routed number, the stored number and the UI
    #    number are one number. Grounding is reported separately and never folded in.
    overall_conf = compute_overall_confidence(extraction_result)

    summary = grounding_summary(grounded_flags)
    if not grounder.available:
        logger.info(f"[Extraction] {document_id}: no token stream — grounding unknown for all fields.")
    elif ungrounded:
        logger.warning(
            f"[Extraction] {document_id}: {summary['ungrounded']}/{summary['checked']} extracted value(s) "
            f"NOT found in the document: {', '.join(ungrounded[:10])}"
            f"{' …' if len(ungrounded) > 10 else ''}"
        )
    if missing_conf:
        # Headers AND line-item cells. If the model ignored the {value, confidence} schema entirely this
        # list holds every populated field, the mean has nothing to average, and the document scores 0.0 —
        # which routes it to review. That is the intended failure direction, but it should be loud.
        logger.warning(
            f"[Extraction] {document_id}: the model returned no confidence for {len(missing_conf)} of "
            f"{len(grounded_flags)} populated field(s) — stored as NULL and excluded from the mean "
            f"(overall={overall_conf:.4f}): {', '.join(missing_conf[:10])}"
            f"{' …' if len(missing_conf) > 10 else ''}"
        )

    # 5. Persist overall confidence on the document row.
    doc = await session.get(IdpDocument, document_id)
    if doc:
        doc.overall_confidence = overall_conf
        session.add(doc)

    await session.commit()
    return overall_conf
