import json
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage

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
