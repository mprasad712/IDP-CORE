"""Layout-preserving text reconstruction from OCR word-box tokens.

Ported from the legacy IDP pipeline's ``ocr/restruct.py``. Groups tokens into
lines by vertical proximity (10 px threshold), merges horizontally-adjacent
fragments, then places each segment at its proportional text column so that
columns, tables, and indentation survive the OCR→text round-trip.

Spell-correction (Levenshtein + word-dict) from the original is intentionally
omitted — it was data-file dependent and fragile across document types.
"""

from __future__ import annotations

_PAGE_HEADER = "===== PAGE {n} ====="


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bounds(bbox: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _make_break(line_items: list) -> list:
    """Merge horizontally-adjacent word fragments into text segments.

    ``line_items`` — list of ``[left, top, w, h, text]``, pre-sorted by left.
    Returns list of ``[left, top, w, h, merged_text]`` segments.

    Gap rules (pixel distance between end of previous token and start of next):
      ≤  5 px  — glue directly (handles split single/double-char tokens)
      5–30 px  — single space
      > 30 px  — new segment (column break)
    """
    prev_right: int = -100
    prev_text: str = ""
    seg_texts: list[str] = []
    seg_coords: list[list] = []

    for item in line_items:
        x, y, w, h, text = item
        gap = x - prev_right
        if gap <= 5 and (len(text) in (1, 2) or len(prev_text) in (1, 2)):
            prev_right = x + w
            prev_text += text
        elif 5 < gap <= 30:
            prev_right = x + w
            prev_text += " " + text
        else:
            # start of a new segment — flush current accumulation
            seg_texts.append(prev_text)
            seg_coords.append([x, y, w, h])
            prev_text = text
            prev_right = x + w

    seg_texts.append(prev_text)  # last accumulated segment

    # Drop the empty leading entry created before the very first word
    if seg_texts and seg_texts[0] == "":
        seg_texts = seg_texts[1:]

    # seg_coords[i] ↔ seg_texts[i]
    return [seg_coords[i] + [seg_texts[i]] for i in range(min(len(seg_coords), len(seg_texts)))]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_page(tokens: list[dict]) -> str:
    """Reconstruct layout-preserving text for a single page's token list.

    Tokens without bounding boxes (e.g. spreadsheet cells) are joined in order.
    """
    boxed = [t for t in tokens if t.get("bounding_box")]
    if not boxed:
        return "\n".join(
            str(t.get("text", "")).strip()
            for t in tokens
            if str(t.get("text", "")).strip()
        )

    # Build [left, top, w, h, text] items
    items: list[list] = []
    for t in boxed:
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        left, top, right, bottom = _bounds(t["bounding_box"])
        items.append([int(left), int(top), int(max(1.0, right - left)), int(max(1.0, bottom - top)), text])

    if not items:
        return ""

    items.sort(key=lambda z: z[1])  # sort by top (Y)

    # Derive page geometry and font size from the token population
    font_size = sum(item[3] for item in items) / len(items)
    page_left  = min(item[0] for item in items)
    page_right = max(item[0] + item[2] for item in items)
    content_w  = max(1.0, page_right - page_left)

    # Column-index mapping  (adapted from legacy Find_text_index)
    # Maps pixel-x → integer text column (0 … sigma2)
    sigma2 = 133.0          # text columns across the usable width
    mu2    = sigma2 / 2.0   # centre column
    scale  = font_size / 21.0
    sigma1 = content_w * scale
    mu1    = ((page_left + page_right) / 2.0) * scale

    def _col(x: float) -> int:
        return int(round(mu2 + sigma2 * (x * scale - mu1) / max(sigma1, 1.0)))

    # Group into lines by vertical proximity (10 px, matching legacy threshold)
    lines: list[list[list]] = []
    cur  = [items[0]]
    prev = items[0][1]
    for item in items[1:]:
        if item[1] - prev < 10:
            cur.append(item)
            prev = item[1]
        else:
            lines.append(cur)
            cur  = [item]
            prev = item[1]
    lines.append(cur)

    out: list[str] = []
    for line in lines:
        line.sort(key=lambda z: z[0])
        row = ""
        for seg in _make_break(line):
            col    = _col(seg[0])
            spaces = max(0, col - len(row))
            row   += " " * spaces + seg[4]
        out.append(row.rstrip())

    return "\n".join(out)


def build_merged_text(tokens: list[dict], page_header: str = _PAGE_HEADER) -> str:
    """Single merged, page-numbered, layout-reconstructed text across all pages."""
    by_page: dict[int, list[dict]] = {}
    for t in tokens:
        by_page.setdefault(int(t.get("page_number", 1) or 1), []).append(t)
    chunks: list[str] = []
    for n in sorted(by_page):
        chunks.append(page_header.format(n=n))
        chunks.append(reconstruct_page(by_page[n]))
    return "\n".join(chunks).strip()
