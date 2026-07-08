from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from uuid import UUID, uuid4
from loguru import logger
import fitz  # PyMuPDF

from agentcore.services.database.models.idp.documents import IdpDocument
from agentcore.services.idp.restruct import reconstruct_page


def _split_storage_path(file_path: str) -> tuple[str, str]:
    """Helper to split a storage file_path like '{agent_id}/{name}' into (agent_id, name)."""
    if "/" in file_path:
        agent_part, name = file_path.split("/", 1)
        return agent_part, name
    return "", file_path


def detect_document_boundaries(
    tokens: list[dict],
    page_status: dict,
) -> list[tuple[int, int]]:
    """Detect page boundaries in a multi-document layout using heuristics.

    Heuristics:
      1. Pages starting with standard header titles (e.g. Invoice, PO) near the top.
      2. Page number format matches 'Page 1' or resets.
      3. Blank separator pages (split boundary occurs on next non-blank page).

    Args:
        tokens: List of OCR tokens with page_number fields (1-indexed).
        page_status: Dict containing metadata, e.g. {"page_count": int}.

    Returns:
        A list of page index ranges (start_page, end_page) inclusive, using 0-based indices.
    """
    # Group tokens by page index (0-based)
    by_page: dict[int, list[dict]] = {}
    for t in tokens:
        p_num = t.get("page_number", 1)
        # Handle cases where page_number is not set or invalid
        try:
            p_idx = int(p_num or 1) - 1
        except (ValueError, TypeError):
            p_idx = 0
        if p_idx < 0:
            p_idx = 0
        by_page.setdefault(p_idx, []).append(t)

    # Reconstruct text per page
    page_texts: dict[int, str] = {}
    for p_idx, p_tokens in by_page.items():
        page_texts[p_idx] = reconstruct_page(p_tokens)

    # Total pages count
    max_pages = max(list(by_page.keys()) + [page_status.get("page_count", 0) - 1], default=-1) + 1
    if max_pages <= 0:
        return []

    # Heuristic values
    header_terms = [
        "invoice", "purchase order", "receipt", "bill of lading", "packing list", 
        "statement", "delivery note", "credit note", "debit note", "purchase confirmation", 
        "order confirmation", "quote", "proforma invoice", "tax invoice", "remittance advice", 
        "bill"
    ]
    page_one_pattern = re.compile(r"\bpage\s+0*1\b", re.IGNORECASE)

    # Detect start pages of sub-documents
    starts = [0]
    for i in range(1, max_pages):
        page_text = page_texts.get(i, "").strip()
        
        # Check if previous page was blank separator
        prev_text = page_texts.get(i - 1, "").strip()
        is_prev_blank = len(prev_text) < 10
        
        # Check standard header on the first few lines
        lines = page_text.lower().split("\n")[:3]
        top_text = " ".join(lines)
        is_header = False
        for term in header_terms:
            if term in top_text:
                is_header = True
                break
                
        # Check page 1 reset
        is_page_one = bool(page_one_pattern.search(page_text))
        
        # If this page is blank, it doesn't start a document.
        # Otherwise, check if it starts a new document.
        if len(page_text) >= 10:
            if is_prev_blank or is_header or is_page_one:
                if i not in starts:
                    starts.append(i)

    # Form ranges, skipping blank separators
    boundaries = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else max_pages - 1
        
        # Trim blank pages from end of document range
        while end > start and len(page_texts.get(end, "").strip()) < 10:
            end -= 1
        # Trim blank pages from start of document range
        while start < end and len(page_texts.get(start, "").strip()) < 10:
            start += 1
            
        if len(page_texts.get(start, "").strip()) >= 10:
            boundaries.append((start, end))

    return boundaries


# ── hybrid multi-doc boundary detection (rules + optional LLM) ───────────────
_PAGE_ONE_RE = re.compile(r"\bpage\s+0*1\b", re.IGNORECASE)
_HEADER_KEYWORDS = (
    "invoice", "delivery note", "purchase order", "statement", "receipt",
    "aadhaar", "pan", "passport", "kyc", "agreement", "terms",
)
_LLM_TIMEOUT = 60  # seconds


def _boundaries_from_page_texts(
    page_texts: dict[int, str],
    page_count: int,
) -> tuple[list[tuple[int, int]], bool, bool]:
    """Textual heuristic boundaries from per-page text.

    Returns (0-based inclusive ranges, has_strong, has_header_hint).
    Only RELIABLE signals — a blank separator page or a 'Page 1' reset — create a
    boundary here (has_strong). A header keyword on a later page is a WEAK hint only:
    it does NOT split (that would falsely cut a long single document whose sections
    reuse keywords); it just sets has_header_hint so the hybrid layer escalates to
    the LLM. Never raises.
    """
    max_pages = page_count if page_count and page_count > 0 else (max(page_texts.keys(), default=-1) + 1)
    if max_pages <= 0:
        return [], False, False

    starts = [0]
    has_strong = False
    has_header_hint = False
    for i in range(1, max_pages):
        text = (page_texts.get(i) or "").strip()
        if len(text) < 10:
            continue  # blank page never STARTS a document
        prev = (page_texts.get(i - 1) or "").strip()
        is_prev_blank = len(prev) < 10
        is_page_one = bool(_PAGE_ONE_RE.search(text))
        first_lines = "\n".join(text.lower().split("\n")[:3])
        is_header = any(k in first_lines for k in _HEADER_KEYWORDS)
        if is_prev_blank or is_page_one:
            starts.append(i)             # STRONG signal -> a real boundary
            has_strong = True
        elif is_header:
            has_header_hint = True        # WEAK hint only -> do NOT split; escalate to LLM

    starts = sorted(set(starts))
    boundaries: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else max_pages - 1
        # trim near-blank separator pages (< 10 chars, to absorb OCR page-number noise) from both ends
        while end > start and len((page_texts.get(end) or "").strip()) < 10:
            end -= 1
        while start < end and len((page_texts.get(start) or "").strip()) < 10:
            start += 1
        if len((page_texts.get(start) or "").strip()) >= 10 or start == end:
            boundaries.append((start, end))
    return (boundaries or [(0, max_pages - 1)]), has_strong, has_header_hint


async def _boundaries_via_llm(
    page_texts: dict[int, str],
    page_images: list[tuple[bytes, str]],
    llm_model,
    page_count: int,
) -> tuple[list[tuple[int, int]] | None, list[str] | None]:
    """Ask the (optional, possibly vision-capable) LLM which pages START a new document AND each type.

    Sends per-page text and, when available, page images. Returns (0-based inclusive ranges,
    per-segment types) or (None, None) on failure. Never raises.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    import base64

    n = page_count if page_count and page_count > 0 else (max(page_texts.keys(), default=-1) + 1)
    if n <= 1:
        return [(0, max(0, n - 1))], None

    sys_prompt = (
        "You segment a multi-document PDF. Given each page's text (and image when provided), decide which "
        "pages START a new document and the TYPE of each document (e.g. Invoice, Delivery Note, Aadhaar, "
        "PAN, Medical Report, Terms). A bundle may contain several documents. Page 1 always starts the "
        "first document. Respond ONLY with JSON: {\"segments\": [{\"start_page\": 1, \"type\": \"Invoice\"}, ...]} "
        "(1-based page numbers, ascending, always including page 1; use \"unknown\" if unsure of a type)."
    )
    text_block = "\n".join(f"--- PAGE {i + 1} ---\n{(page_texts.get(i) or '')[:800]}" for i in range(n))
    content: list = [{"type": "text", "text": text_block}]
    for img_bytes, mime in (page_images or []):
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=content)]

    try:
        response = await asyncio.wait_for(llm_model.ainvoke(messages), timeout=_LLM_TIMEOUT)
        raw = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[split] LLM boundary detection failed: {exc}")
        return None, None

    try:
        content_str = raw.strip()
        if content_str.startswith("```"):
            lines = content_str.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content_str = "\n".join(lines).strip()
        segments = json.loads(content_str).get("segments") or []
        pairs: dict[int, str] = {}
        for seg in segments:
            s = max(0, int(seg.get("start_page", 1)) - 1)
            if s < n:
                pairs[s] = str(seg.get("type") or "unknown")
        pairs.setdefault(0, "unknown")
        starts = sorted(pairs.keys())
        boundaries, types = [], []
        for idx, s in enumerate(starts):
            e = starts[idx + 1] - 1 if idx + 1 < len(starts) else n - 1
            boundaries.append((s, e))
            types.append(pairs[s])
        return (boundaries or [(0, n - 1)]), (types or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[split] LLM boundary parse failed: {exc}")
        return None, None


async def detect_boundaries_hybrid(
    page_texts: dict[int, str],
    page_images: list[tuple[bytes, str]],
    llm_model,
    page_count: int,
) -> tuple[list[tuple[int, int]], list[str] | None]:
    """Hybrid detection that works in ALL scenarios and never false-splits without confirmation.

    Returns (boundaries, seg_types); seg_types (per-boundary type strings) is present only when
    the LLM path ran, else None. Rules yield a boundary ONLY on strong signals (blank page /
    'Page 1'); header hints escalate to the LLM when a model is connected; no model -> strong
    splits only; LLM fail -> conservative. Never raises.
    """
    n = page_count if page_count and page_count > 0 else (max(page_texts.keys(), default=-1) + 1)
    single = [(0, n - 1)] if n >= 1 else []
    boundaries, has_strong, has_header_hint = _boundaries_from_page_texts(page_texts, page_count)
    # has_header_hint isn't gated on: ANY non-confident multi-page doc escalates to the LLM (safer)
    confident = has_strong and len(boundaries) >= 2

    if llm_model is None:
        return (boundaries if confident else single), None
    if confident:
        return boundaries, None
    if n <= 1:
        return single, None
    llm_boundaries, seg_types = await _boundaries_via_llm(page_texts, page_images, llm_model, page_count)
    if llm_boundaries and len(llm_boundaries) >= 2:
        return llm_boundaries, seg_types
    return (boundaries if confident else single), None


async def materialize_children(
    session: Any,
    storage: Any,
    parent_doc: IdpDocument,
    boundaries: list[tuple[int, int]],
) -> list[UUID]:
    """Fetch the parent PDF, extract page ranges, and insert new child IdpDocument entries.

    Args:
        session: DB session.
        storage: Storage service instance.
        parent_doc: The parent IdpDocument row.
        boundaries: List of (start_page, end_page) page ranges (0-based, inclusive).

    Returns:
        List of created child UUIDs.
    """
    if not boundaries:
        return []

    if parent_doc.file_type.lower() != "pdf":
        logger.warning(f"Document {parent_doc.id} file type '{parent_doc.file_type}' is not PDF. Splitting skipped.")
        return []

    # Get parent PDF bytes
    agent_part, file_name = _split_storage_path(parent_doc.file_path)
    file_bytes = await storage.get_file(agent_part, file_name)

    # Open PDF
    pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
    child_ids: list[UUID] = []
    saved_files: list[str] = []  # track stored child files to clean up if the commit fails

    for start, end in boundaries:
        # Extract pages
        child_pdf = fitz.open()
        child_pdf.insert_pdf(pdf_doc, from_page=start, to_page=end)
        child_bytes = child_pdf.tobytes()
        child_pdf.close()

        # Save to storage
        child_id = uuid4()
        child_file_name = f"split_child_{child_id}.pdf"
        await storage.save_file(
            agent_id=agent_part,
            file_name=child_file_name,
            data=child_bytes,
        )
        saved_files.append(child_file_name)

        # Config override to prevent infinite recursion
        child_extra = (parent_doc.extra or {}).copy()
        child_extra["multi_doc_split"] = False

        child_file_path = f"{agent_part}/{child_file_name}"
        orig_stem = Path(parent_doc.original_filename).stem
        child_orig_filename = f"{orig_stem}_split_{start+1}_{end+1}.pdf"

        # Create child IdpDocument DB record
        child_doc = IdpDocument(
            id=child_id,
            agent_id=parent_doc.agent_id,
            parent_document_id=parent_doc.id,
            original_filename=child_orig_filename,
            file_path=child_file_path,
            file_type="pdf",
            mime_type="application/pdf",
            file_size_bytes=len(child_bytes),
            page_count=(end - start + 1),
            checksum=None,
            source=parent_doc.source,
            connector_id=parent_doc.connector_id,
            source_metadata=parent_doc.source_metadata,
            predicted_type=None,
            status="queued",
            uploaded_by=parent_doc.uploaded_by,
            extra=child_extra,
        )
        session.add(child_doc)
        child_ids.append(child_id)

    pdf_doc.close()
    try:
        await session.commit()
    except Exception:
        # The child rows failed to commit — delete the child files we already wrote so storage
        # isn't left with orphans the DB doesn't reference. Best-effort; re-raise the original.
        await session.rollback()
        for fn in saved_files:
            try:
                await storage.delete_file(agent_id=agent_part, file_name=fn)
            except Exception:  # pragma: no cover - cleanup is best-effort
                logger.warning(f"[Multi-Doc Split] could not clean up orphaned split file {fn}")
        raise

    logger.info(f"[Multi-Doc Split] Created {len(child_ids)} child documents from parent {parent_doc.id}.")
    return child_ids
