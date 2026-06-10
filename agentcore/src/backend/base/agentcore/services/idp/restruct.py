"""Layout-preserving text reconstruction from OCR word boxes.

Ported/simplified from the legacy extraction app's ``ocr/restruct.py``: groups tokens
into lines by vertical position, orders each line by horizontal position, and inserts
spacing proportional to horizontal gaps so columns/tables stay readable. The legacy
dictionary spell-correction is intentionally dropped (fragile, data-file dependent).

Operates on in-memory tokens ``[{text, bounding_box, page_number, confidence}]`` where
``bounding_box`` is 4 ``[x, y]`` corners (or ``None`` for box-less tokens, e.g. office
cells, which are joined in order).
"""

from __future__ import annotations

_PAGE_HEADER = "===== PAGE {n} ====="


def _bounds(bbox: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def reconstruct_page(tokens: list[dict]) -> str:
    """Reconstruct layout-preserving text for a single page's tokens."""
    boxed = [t for t in tokens if t.get("bounding_box")]
    if not boxed:
        return "\n".join(
            str(t.get("text", "")).strip() for t in tokens if str(t.get("text", "")).strip()
        )

    items: list[dict] = []
    for t in boxed:
        text = str(t.get("text", ""))
        if not text.strip():
            continue
        left, top, right, bottom = _bounds(t["bounding_box"])
        items.append({"text": text, "left": left, "top": top, "right": right, "h": max(1.0, bottom - top)})
    if not items:
        return ""

    items.sort(key=lambda z: z["top"])
    med_h = sorted(i["h"] for i in items)[len(items) // 2]
    line_thresh = max(4.0, med_h * 0.6)

    # group tokens into lines by vertical proximity
    lines: list[list[dict]] = []
    cur = [items[0]]
    base = items[0]["top"]
    for it in items[1:]:
        if abs(it["top"] - base) <= line_thresh:
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
        base = it["top"]
    lines.append(cur)

    # median per-character width, for proportional gap spacing
    widths = [(i["right"] - i["left"]) / max(1, len(i["text"])) for i in items if i["right"] > i["left"]]
    char_w = (sorted(widths)[len(widths) // 2] if widths else 6.0) or 6.0

    out: list[str] = []
    for line in lines:
        line.sort(key=lambda z: z["left"])
        s = ""
        prev_right: float | None = None
        for it in line:
            if prev_right is None:
                s = it["text"]
            else:
                gap = it["left"] - prev_right
                spaces = 1 if gap <= char_w else min(40, max(1, round(gap / char_w)))
                s += " " * spaces + it["text"]
            prev_right = it["right"]
        out.append(s.rstrip())
    return "\n".join(out)


def build_merged_text(tokens: list[dict], page_header: str = _PAGE_HEADER) -> str:
    """Single merged, page-numbered, layout-reconstructed text across all pages.

    Page headers act as citations so any extracted value can be traced to its page.
    """
    by_page: dict[int, list[dict]] = {}
    for t in tokens:
        by_page.setdefault(int(t.get("page_number", 1) or 1), []).append(t)
    chunks: list[str] = []
    for n in sorted(by_page):
        chunks.append(page_header.format(n=n))
        chunks.append(reconstruct_page(by_page[n]))
    return "\n".join(chunks).strip()
