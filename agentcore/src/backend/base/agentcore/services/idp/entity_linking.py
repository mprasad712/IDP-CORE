"""Cross-page entity linking (B36) — pragmatic first-cut.

Backend-automatic feature (no canvas node): after extraction, identify entity values that
appear on more than one page (e.g. a vendor name or invoice number repeated in a header and a
continuation page) and record them in ``idp_entity_links`` with their per-page occurrences.
This gives the review UI a way to show "this value appears on pages 1 and 3" and is the seed
for richer hierarchical extraction later.

v1 scope: link the extracted HEADER values against the per-page text (so multi-word values
like a vendor name match), keeping only entities seen on >= 2 distinct pages. Line-item-level
linking and true entity resolution (fuzzy / NER) are deferred to v2.
"""

from __future__ import annotations

import re
from uuid import UUID


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _header_values(extracted: dict) -> list[tuple[str, str]]:
    """Return ``(field_name, value)`` pairs for non-trivial header values."""
    out: list[tuple[str, str]] = []
    for name, field in (extracted.get("headers") or {}).items():
        value = field.get("value") if isinstance(field, dict) else field
        if value is None:
            continue
        if len(_norm(value)) >= 3:  # skip empty / 1-2 char noise
            out.append((str(name), str(value)))
    return out


def _page_texts(tokens: list[dict]) -> dict[int, str]:
    """Normalized concatenated text per page (so multi-word values can be matched)."""
    by_page: dict[int, list[str]] = {}
    for t in tokens:
        p = int(t.get("page_number", 1) or 1)
        by_page.setdefault(p, []).append(str(t.get("text", "")))
    return {p: _norm(" ".join(parts)) for p, parts in by_page.items()}


async def link_entities(session, document_id: UUID, extracted: dict, tokens: list[dict]) -> int:
    """Write ``idp_entity_links`` for header values seen on >= 2 pages. Returns the count.

    Non-fatal by contract (the pipeline wraps the call); commits internally so links persist
    independently of later steps. Idempotent within a run via an in-memory ``seen`` guard.
    """
    from agentcore.services.database.models.idp.documents import IdpEntityLink

    if not isinstance(extracted, dict) or not tokens:
        return 0

    page_norm = _page_texts(tokens)
    seen: set[str] = set()
    created = 0

    for field_name, value in _header_values(extracted):
        target = _norm(value)
        key = target[:200]
        if key in seen:
            continue
        pages = sorted(p for p, txt in page_norm.items() if target and target in txt)
        if len(pages) < 2:
            continue  # not a cross-page entity
        seen.add(key)
        session.add(
            IdpEntityLink(
                document_id=document_id,
                entity_key=key,
                entity_type=str(field_name)[:60],
                occurrences={"pages": pages, "value": str(value)[:500]},
            )
        )
        created += 1

    if created:
        await session.commit()
    return created
