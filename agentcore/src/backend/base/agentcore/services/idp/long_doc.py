"""Long-document handling (B35) — pragmatic first-cut.

Backend-automatic feature (no canvas node): when a document is long (many pages or a large
token estimate), extracting the whole thing in one LLM call loses accuracy and can blow the
context window. This module:

  * decides whether a document needs chunking (``should_chunk``),
  * derives a section tree from the page-numbered tokens (``build_sections``) and persists it
    to ``idp_document_sections`` (``persist_sections``) for traceability,
  * groups the tokens into page-numbered, token-bounded chunks for extraction
    (``chunks_for_extraction``), and
  * merges the per-chunk extraction results back into one header/line-item structure
    (``merge_chunk_extractions``) — header conflicts resolved by highest confidence, line
    items concatenated with a continuous ``row_index``.

It operates on the same in-memory tokens the pipeline already has
(``[{text, bounding_box, page_number, confidence}]``) and reuses ``restruct.build_merged_text``
so every chunk keeps its ``===== PAGE N =====`` citations. Full cross-page table stitching is
deferred to v2 (kept out of scope deliberately).
"""

from __future__ import annotations

import re
from uuid import UUID

from agentcore.services.idp.restruct import build_merged_text

# A line that looks like a section heading: markdown ``#..####`` or a short ALL-CAPS line.
_HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s+\S|[A-Z][A-Z0-9 &/\-]{3,60})\s*$")
# Page-citation header emitted by restruct.build_merged_text.
_PAGE_HEADER_RE = re.compile(r"^=====\s*PAGE\s+(\d+)\s*=====$")


def _est_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — avoids a hard tiktoken dependency here."""
    return max(1, len(text) // 4)


def _pages(tokens: list[dict]) -> list[int]:
    return sorted({int(t.get("page_number", 1) or 1) for t in tokens})


def _tokens_for_pages(tokens: list[dict], page_start: int, page_end: int) -> list[dict]:
    return [t for t in tokens if page_start <= int(t.get("page_number", 1) or 1) <= page_end]


def should_chunk(tokens: list[dict], *, max_pages: int = 8, max_tokens: int = 12000) -> bool:
    """True when the document is long enough to benefit from section-aware chunking."""
    if not tokens:
        return False
    pages = _pages(tokens)
    if len(pages) > max(1, max_pages):
        return True
    total_text = " ".join(str(t.get("text", "")) for t in tokens)
    return _est_tokens(total_text) > max(1, max_tokens)


def _first_heading_on_page(tokens: list[dict], page: int) -> str | None:
    """Return the first heading-like line on a page, else None."""
    page_text = build_merged_text(_tokens_for_pages(tokens, page, page))
    for raw in page_text.splitlines():
        line = raw.strip()
        if not line or _PAGE_HEADER_RE.match(line):
            continue
        if _HEADING_RE.match(line):
            return re.sub(r"^#{1,4}\s+", "", line).strip()[:300]
    return None


def build_sections(tokens: list[dict]) -> list[dict]:
    """Derive a flat list of sections (page ranges) from the page-numbered tokens.

    Heuristic: start a new section whenever a page opens with a heading-like line; otherwise
    extend the current section. With no headings anywhere, the whole document is one section.
    Returns ``[{title, page_start, page_end, order_index, parent_idx}]`` ordered by page.
    """
    pages = _pages(tokens)
    if not pages:
        return []

    sections: list[dict] = []
    for page in pages:
        heading = _first_heading_on_page(tokens, page)
        if heading is not None or not sections:
            sections.append(
                {
                    "title": heading or "Document",
                    "page_start": page,
                    "page_end": page,
                    "order_index": len(sections),
                    "parent_idx": None,
                }
            )
        else:
            sections[-1]["page_end"] = page
    return sections


async def persist_sections(session, document_id: UUID, sections: list[dict]) -> None:
    """Insert ``idp_document_sections`` rows (flat hierarchy v1). Commits internally."""
    from agentcore.services.database.models.idp.documents import IdpDocumentSection

    if not sections:
        return
    for s in sections:
        session.add(
            IdpDocumentSection(
                document_id=document_id,
                parent_section_id=None,
                title=s.get("title"),
                page_start=int(s["page_start"]),
                page_end=int(s["page_end"]),
                order_index=int(s.get("order_index", 0)),
            )
        )
    await session.commit()


def _split_text_by_tokens(text: str, limit: int, overlap: int = 0) -> list[str]:
    """Fixed-size split of one text block into ~``limit``-token pieces (token ~ 4 chars) with overlap.

    Breaks at the nearest newline/space before the cut so lines aren't sliced mid-word. If the
    block opens with a ``===== PAGE N =====`` citation header, that header is repeated at the
    top of every piece so each sub-chunk stays attributable to its page.
    """
    max_chars = max(4000, limit * 4)
    overlap_chars = max(0, overlap * 4)
    step = max_chars - overlap_chars
    if step <= 0:
        step = max_chars // 2

    if len(text) <= max_chars:
        return [text] if text.strip() else []

    # Preserve the page-citation header across sub-chunks.
    header = ""
    first_line = text.split("\n", 1)[0].strip()
    if _PAGE_HEADER_RE.match(first_line):
        header = first_line + "\n"

    parts: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(n, i + max_chars)
        if end < n:
            brk = text.rfind("\n", i, end)
            if brk <= i:
                brk = text.rfind(" ", i, end)
            if brk > i:
                end = brk
        piece = text[i:end]
        if piece.strip():
            # Re-attach the header to continuation pieces (the first piece already has it).
            parts.append(piece if (not parts or not header) else header + piece)
        
        if end >= n:
            break
        i = max(i + 1, end - overlap_chars)
    return parts


def chunks_for_extraction(
    tokens: list[dict], sections: list[dict], *, max_tokens: int = 12000, overlap_tokens: int = 256
) -> list[str]:
    """Group tokens into page-numbered chunk texts, each roughly ``<= max_tokens``, respecting overlap.

    Packs whole sections together while they fit; a single oversized section is split by page,
    and a single page that ALONE exceeds the limit is further fixed-token-split so no chunk
    blows the model context. Always returns at least one non-empty chunk.
    """
    limit = max(1000, max_tokens)
    chunks: list[str] = []

    if not sections:
        merged = build_merged_text(tokens)
        return [merged] if merged.strip() else []

    cur_pages: list[int] = []
    cur_tokens = 0

    def _flush() -> None:
        nonlocal cur_pages, cur_tokens
        if not cur_pages:
            return
        sub = [t for t in tokens if int(t.get("page_number", 1) or 1) in set(cur_pages)]
        text = build_merged_text(sub)
        if text.strip():
            chunks.append(text)
        cur_pages = []
        cur_tokens = 0

    for s in sections:
        for page in range(int(s["page_start"]), int(s["page_end"]) + 1):
            page_text = build_merged_text(_tokens_for_pages(tokens, page, page))
            page_tokens = _est_tokens(page_text)
            # A single page bigger than the whole limit can't be packed — flush what we
            # have and emit it as its own fixed-token-split sub-chunks.
            if page_tokens > limit:
                _flush()
                chunks.extend(_split_text_by_tokens(page_text, limit, overlap_tokens))
                continue
            if cur_pages and cur_tokens + page_tokens > limit:
                _flush()
            cur_pages.append(page)
            cur_tokens += page_tokens
    _flush()

    if not chunks:
        merged = build_merged_text(tokens)
        return [merged] if merged.strip() else []
    return chunks


def merge_chunk_extractions(results: list[dict], strategy: str = "keep_highest_confidence") -> dict:
    """Merge per-chunk extraction dicts into one ``{headers, line_items}`` result based on strategy.

    Strategies:
      * keep_highest_confidence: Keep header field with the highest confidence score.
      * keep_first: Keep the first non-null header field value encountered.
      * merge_all: Concatenate string values if they differ.
    """
    merged_headers: dict = {}
    merged_lines: list = []
    errors: list[str] = []

    for res in results:
        if not isinstance(res, dict):
            continue
        if res.get("error"):
            errors.append(str(res["error"]))

        for name, field in (res.get("headers") or {}).items():
            if not isinstance(field, dict):
                continue
            
            value = field.get("value")
            if value is None:
                continue

            existing = merged_headers.get(name)
            if existing is None:
                merged_headers[name] = field.copy()
            else:
                if strategy == "keep_first":
                    # Already have the first one, do nothing
                    pass
                elif strategy == "merge_all":
                    # Concatenate if they are different
                    exist_val = existing.get("value")
                    if exist_val is not None and str(exist_val).strip() != str(value).strip():
                        existing["value"] = f"{exist_val}, {value}"
                    existing["confidence"] = max(existing.get("confidence", 0.0), field.get("confidence", 0.0))
                else:  # keep_highest_confidence
                    new_conf = field.get("confidence", 0.0)
                    old_conf = existing.get("confidence", 0.0)
                    if new_conf > old_conf:
                        merged_headers[name] = field.copy()

        for row in res.get("line_items") or []:
            merged_lines.append(row)

    # Re-index nested rows continuously; leave flat rows for the save-time normaliser.
    counter = 0
    for row in merged_lines:
        if isinstance(row, dict) and "columns" in row:
            row["row_index"] = counter
            counter += 1

    out: dict = {"headers": merged_headers, "line_items": merged_lines}
    if errors and not merged_headers and not merged_lines:
        out["error"] = "; ".join(errors[:3])
    return out
