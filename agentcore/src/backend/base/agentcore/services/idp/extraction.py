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

async def extract_dynamic(
    ocr_text: str,
    prompt: str,
    llm_model: Any
) -> Dict[str, Any]:
    """Extract structured data dynamically based on a freeform user prompt.

    Supports structured tool calling when available on the LLM, with fallback JSON parsing.
    """
    if llm_model is None:
        raise ValueError("Language Model is required for dynamic prompting extraction.")

    user_content = f"Instruction: {prompt}\n\nDocument Text:\n{ocr_text}"
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_content)
    ]

    # Try utilizing structured output functionality in LangChain if supported
    if hasattr(llm_model, "with_structured_output"):
        try:
            structured_model = llm_model.with_structured_output(StructuredExtractionResult)
            result = await structured_model.ainvoke(messages)
            if isinstance(result, StructuredExtractionResult):
                return result.model_dump()
            elif isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning(f"[Extraction] with_structured_output failed: {e}. Falling back to raw JSON parsing.")

    # Fallback: standard invocation with raw JSON parsing
    try:
        response = await llm_model.ainvoke(messages)
        raw_content = response.content if hasattr(response, "content") else str(response)
        
        # Clean markdown formatting if present
        raw_content = raw_content.strip()
        if raw_content.startswith("```"):
            lines = raw_content.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw_content = "\n".join(lines).strip()

        parsed = json.loads(raw_content)
        
        # Basic validation of the dictionary structure
        if not isinstance(parsed, dict):
            raise ValueError("Parsed output is not a dictionary.")
            
        # Ensure 'headers' and 'line_items' exist
        if "headers" not in parsed:
            parsed["headers"] = {}
        if "line_items" not in parsed:
            parsed["line_items"] = []

        # Validate structure to match StructuredExtractionResult schema
        # (This cleans up any deviations the LLM made)
        clean_headers = {}
        for k, v in parsed["headers"].items():
            if isinstance(v, dict):
                clean_headers[k] = {
                    "value": str(v.get("value")) if v.get("value") is not None else None,
                    "confidence": float(v.get("confidence", 0.8)),
                    "reasoning": str(v.get("reasoning", "")) if v.get("reasoning") is not None else None
                }
            else:
                clean_headers[k] = {
                    "value": str(v) if v is not None else None,
                    "confidence": 0.8,
                    "reasoning": "Direct extraction"
                }

        clean_line_items = []
        for idx, row in enumerate(parsed["line_items"]):
            if not isinstance(row, dict):
                continue
            row_idx = row.get("row_index", idx)
            cols = row.get("columns", [])
            clean_cols = []
            for col in cols:
                if not isinstance(col, dict):
                    continue
                clean_cols.append({
                    "column_name": str(col.get("column_name", "")),
                    "value": str(col.get("value")) if col.get("value") is not None else None,
                    "confidence": float(col.get("confidence", 0.8)),
                    "reasoning": str(col.get("reasoning", "")) if col.get("reasoning") is not None else None
                })
            clean_line_items.append({
                "row_index": int(row_idx),
                "columns": clean_cols
            })

        return {
            "headers": clean_headers,
            "line_items": clean_line_items
        }

    except Exception as e:
        logger.error(f"[Extraction] Dynamic prompting extraction parsing failed: {e}")
        # Return empty structured schema in case of parsing errors to prevent system crash
        return {
            "headers": {},
            "line_items": [],
            "error": f"Extraction parsing failed: {str(e)}"
        }


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

    # 3. Format the instructions/prompt based on configuration
    headers_desc = "\n".join(
        f"- '{h.field_name}' (type: {h.field_type}{f', description: {h.description}' if h.description else ''})"
        for h in headers
    )
    columns_desc = "\n".join(
        f"- '{c.column_name}' (type: {c.column_type})"
        for c in line_items
    )

    prompt = (
        f"You must extract the following specific fields from the document:\n\n"
        f"Header Fields:\n{headers_desc or 'None'}\n\n"
        f"Line Item Columns (Table):\n{columns_desc or 'None'}\n\n"
        "Ensure all extracted values conform to their specified type (number, date, text, boolean). "
        "For dates, return in YYYY-MM-DD format if possible. For numbers, return numeric values as strings. "
        "For boolean, return 'true' or 'false'."
    )

    # 4. Invoke the dynamic prompting extractor with our generated prompt
    raw_result = await extract_dynamic(ocr_text, prompt, llm_model)

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

        # Format the instructions/prompt based on configuration
        headers_desc = "\n".join(
            f"- '{h.field_name}' (type: {h.field_type}{f', description: {h.description}' if h.description else ''})"
            for h in headers
        )
        columns_desc = "\n".join(
            f"- '{c.column_name}' (type: {c.column_type})"
            for c in line_items
        )

        prompt = (
            f"You must extract the following specific fields from the document:\n\n"
            f"Header Fields:\n{headers_desc or 'None'}\n\n"
            f"Line Item Columns (Table):\n{columns_desc or 'None'}\n\n"
            "Ensure all extracted values conform to their specified type (number, date, text, boolean). "
            "For dates, return in YYYY-MM-DD format if possible. For numbers, return numeric values as strings. "
            "For boolean, return 'true' or 'false'."
        )
    else:
        prompt = str(prompt_or_config_id).strip()
        if not prompt:
            prompt = "Extract all key fields from this document. Return as structured JSON."

    # 3. Construct multimodal message payload
    user_content = [
        {
            "type": "text",
            "text": f"Instruction: {prompt}\n\nPlease extract the requested information from the provided document image(s)."
        }
    ]
    for img_bytes, mime in page_images:
        b64_data = base64.b64encode(img_bytes).decode("utf-8")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64_data}"}
        })

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_content)
    ]

    # 4. Invoke the Vision LLM model
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
            logger.warning(f"[Multimodal Extraction] with_structured_output failed: {e}. Falling back to raw JSON parsing.")

    if raw_result is None:
        try:
            response = await llm_model.ainvoke(messages)
            raw_content = response.content if hasattr(response, "content") else str(response)
            
            # Clean markdown formatting if present
            raw_content = raw_content.strip()
            if raw_content.startswith("```"):
                lines = raw_content.split("\n")
                if lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()

            parsed = json.loads(raw_content)
            
            if not isinstance(parsed, dict):
                raise ValueError("Parsed output is not a dictionary.")
                
            if "headers" not in parsed:
                parsed["headers"] = {}
            if "line_items" not in parsed:
                parsed["line_items"] = []

            # Clean and validate structure
            clean_headers = {}
            for k, v in parsed["headers"].items():
                if isinstance(v, dict):
                    clean_headers[k] = {
                        "value": str(v.get("value")) if v.get("value") is not None else None,
                        "confidence": float(v.get("confidence", 0.8)),
                        "reasoning": str(v.get("reasoning", "")) if v.get("reasoning") is not None else None
                    }
                else:
                    clean_headers[k] = {
                        "value": str(v) if v is not None else None,
                        "confidence": 0.8,
                        "reasoning": "Direct extraction"
                    }

            clean_line_items = []
            for idx, row in enumerate(parsed["line_items"]):
                if not isinstance(row, dict):
                    continue
                row_idx = row.get("row_index", idx)
                cols = row.get("columns", [])
                clean_cols = []
                for col in cols:
                    if not isinstance(col, dict):
                        continue
                    clean_cols.append({
                        "column_name": str(col.get("column_name", "")),
                        "value": str(col.get("value")) if col.get("value") is not None else None,
                        "confidence": float(col.get("confidence", 0.8)),
                        "reasoning": str(col.get("reasoning", "")) if col.get("reasoning") is not None else None
                    })
                clean_line_items.append({
                    "row_index": int(row_idx),
                    "columns": clean_cols
                })

            raw_result = {
                "headers": clean_headers,
                "line_items": clean_line_items
            }

        except Exception as e:
            logger.error(f"[Multimodal Extraction] parsing failed: {e}")
            raw_result = {
                "headers": {},
                "line_items": [],
                "error": f"Multimodal extraction parsing failed: {str(e)}"
            }

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

    # 3. Save line items
    line_items_list = extraction_result.get("line_items", [])
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

    # 4. Calculate overall weighted average confidence
    overall_conf = sum(confidences) / len(confidences) if confidences else 1.0

    # 5. Update IdpDocument overall confidence
    doc = await session.get(IdpDocument, document_id)
    if doc:
        doc.overall_confidence = overall_conf
        session.add(doc)

    await session.commit()
    return overall_conf
