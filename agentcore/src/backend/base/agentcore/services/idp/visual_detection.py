from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from uuid import UUID, uuid4

from loguru import logger
from PIL import Image

try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except Exception:
    # Catch broadly, not just ImportError: when pyzbar is installed but its native ZBar
    # library can't load, the import raises OSError/FileNotFoundError (missing libzbar0 on
    # Linux, or the VC++ 2013 runtime on Windows). Degrade to "QR/barcode skipped" instead
    # of crashing the whole module import.
    PYZBAR_AVAILABLE = False

# This module does no OpenCV-based local detection — barcodes/QR use pyzbar (above) and
# signatures/checkboxes/stamps/logos are found by a vision LLM. Exposed as a stable flag so
# callers/tests can branch on it; always False since there is no cv2 code path here.
OPENCV_AVAILABLE = False


# Element types the vision LLM is asked to find. QR / barcodes are handled by the
# deterministic pyzbar decoder (below), NOT the model.
_VLM_ELEMENT_TYPES: tuple[str, ...] = ("signature", "checkbox", "stamp", "logo")
_BARCODE_TYPES: tuple[str, ...] = ("qr", "barcode")

# Safety cap: at most this many pages are sent to the vision model per document,
# so a huge PDF can't fan out into an unbounded number of (paid) VLM calls.
_MAX_VLM_PAGES = 8
# Render DPI for PDF pages. 150 mirrors the extraction vision path; high enough for
# small marks (checkboxes/signatures) without ballooning the image token count.
_RENDER_DPI = 150

_VLM_SYSTEM = (
    "You are an expert document visual-element detector. You examine a page image thoroughly and "
    "report every visual element present. You ALWAYS reply with a single JSON array of element "
    "objects and nothing else — no explanations, no markdown, no code fences."
)

_VLM_PROMPT = (
    "Carefully examine this document page image from top to bottom, left to right, and detect "
    "EVERY occurrence of these element types: {types}.\n\n"
    "Definitions (what to look for):\n"
    "- checkbox: any small square, box, or bracket that can be ticked or marked. Forms almost "
    "always contain several of these (both checked and unchecked). They are small — look closely "
    "across the whole page. Read the printed text beside each one as its label.\n"
    "- signature: a handwritten signature (a cursive name or mark), usually on a signature line.\n"
    "- stamp: an ink or rubber stamp, an official seal, or a watermark.\n"
    "- logo: a company/organisation logo, emblem, or letterhead graphic.\n\n"
    "Be thorough: it is better to report a likely element with a lower confidence than to miss it. "
    "Do NOT report plain printed text, typed field values, or ordinary handwriting as elements — "
    "only the element types listed above.\n\n"
    "Respond with ONLY a JSON array (no prose, no markdown fences). Each item is an object:\n"
    '{{"element_type": <one of {types}>, '
    '"bounding_box": {{"x_min":0..1,"y_min":0..1,"x_max":0..1,"y_max":0..1}}, '
    '"label": <string or null>, "decoded_value": <string or null>, "confidence": 0..1}}\n'
    "Rules:\n"
    "- bounding_box values are fractions of the page width/height (0..1); approximate is fine.\n"
    '- checkbox: decoded_value MUST be exactly "checked" or "unchecked"; AND set "label" to the '
    'option text next to it (e.g. "First return filed for the plan", "Amended return"), or null '
    "if there is no clear label.\n"
    "- signature / stamp / logo: decoded_value is any readable text on it (else null); label = null.\n"
    "- ONLY if the page genuinely contains none of these element types, return exactly [].\n"
    "Output the JSON array:"
)


# ─────────────────────────── page rendering ───────────────────────────
def _render_pages(
    file_bytes: bytes, file_type: str, selected_pages: set[int] | None = None
) -> list[tuple[int, bytes]]:
    """Rasterize a document to ``[(page_number, png_bytes), ...]`` (1-based page numbers).

    PDFs are rendered page-by-page via PyMuPDF; images are treated as a single page 1.
    ``selected_pages`` (1-based) limits which pages are returned; ``None`` = all pages.
    """
    ft = (file_type or "").lower().lstrip(".")
    pages: list[tuple[int, bytes]] = []

    if ft == "pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF not installed — cannot rasterize PDF for visual detection.")
            return []
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as e:
            logger.error(f"Could not open PDF for visual detection: {e}")
            return []
        try:
            for i in range(len(doc)):
                page_number = i + 1
                if selected_pages is not None and page_number not in selected_pages:
                    continue
                try:
                    pages.append((page_number, doc[i].get_pixmap(dpi=_RENDER_DPI).tobytes("png")))
                except Exception as e:
                    logger.warning(f"Failed to render PDF page {page_number} for visual detection: {e}")
        finally:
            doc.close()
    else:
        # Treat any non-PDF as a single-page image.
        if selected_pages is None or 1 in selected_pages:
            pages.append((1, file_bytes))

    return pages


# ─────────────────────────── QR / barcode (pyzbar) ───────────────────────────
def _detect_barcodes(
    file_bytes: bytes, file_type: str, selected_pages: set[int] | None = None
) -> list[dict]:
    """Deterministic QR + barcode detection via pyzbar. Returns the canonical element dicts."""
    if not PYZBAR_AVAILABLE:
        logger.debug("pyzbar is not installed — QR/barcode detection skipped.")
        return []

    out: list[dict] = []
    for page_number, png in _render_pages(file_bytes, file_type, selected_pages):
        try:
            img = Image.open(io.BytesIO(png))
            width, height = img.size
            for obj in pyzbar.decode(img):
                x, y, w, h = obj.rect
                element_type = "qr" if obj.type == "QRCODE" else "barcode"
                try:
                    val = obj.data.decode("utf-8")
                except Exception:
                    val = str(obj.data)
                out.append({
                    "element_type": element_type,
                    "page_number": page_number,
                    "bounding_box": {
                        "x_min": max(0.0, float(x) / width),
                        "y_min": max(0.0, float(y) / height),
                        "x_max": min(1.0, float(x + w) / width),
                        "y_max": min(1.0, float(y + h) / height),
                    },
                    "confidence": 1.0,
                    "decoded_value": val,
                })
        except Exception as e:
            logger.warning(f"pyzbar barcode detection failed on page {page_number}: {e}")
    return out


# ─────────────────────────── vision LLM detection ───────────────────────────
def _clamp(value, *, default: float = 0.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _parse_json_array(raw: str) -> list:
    """Best-effort parse of a list of element objects from a model response.

    Tolerates code fences, leading/trailing prose, a single object, and common wrapper
    objects like ``{"elements": [...]}`` / ``{"detected_elements": [...]}``.
    """
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()

    def _coerce(obj):
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for k in ("elements", "detected_elements", "detections", "items", "results", "objects"):
                v = obj.get(k)
                if isinstance(v, list):
                    return v
            if "element_type" in obj or "type" in obj:
                return [obj]
        return []

    # 1) direct parse (array OR wrapper object)
    try:
        return _coerce(json.loads(s))
    except Exception:
        pass
    # 2) first [...] substring
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    # 3) first {...} substring (wrapper / single object)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            return _coerce(json.loads(m.group(0)))
        except Exception:
            pass
    return []


async def _detect_via_vlm(
    file_bytes: bytes,
    file_type: str,
    llm_model,
    wanted_types: set[str] | None,
    selected_pages: set[int] | None = None,
    diag: dict | None = None,
) -> list[dict]:
    """Detect signature/checkbox/stamp/logo/handwriting per page with a vision LLM.

    One model call per page (page-relative bounding boxes, small prompts, no cross-page
    confusion). Capped at ``_MAX_VLM_PAGES`` pages to bound cost.

    ``diag`` (optional) is populated with observability the pipeline surfaces in the flow log:
    ``pages`` (how many were sent to the model), ``raw_items`` (how many objects the model
    returned before filtering), and ``errors`` (per-page model-call failures).
    """
    if llm_model is None:
        return []

    # Only ask the model for the element types the node actually enabled (None = all VLM types).
    types = [t for t in _VLM_ELEMENT_TYPES if (wanted_types is None or t in wanted_types)]
    if not types:
        return []

    # Rendering is CPU-bound (PyMuPDF rasterization) — keep it off the event loop, or every
    # HTTP request in the process stalls while pages rasterize.
    pages = await asyncio.to_thread(_render_pages, file_bytes, file_type, selected_pages)
    if len(pages) > _MAX_VLM_PAGES:
        logger.info(f"Visual detection: {len(pages)} pages > cap {_MAX_VLM_PAGES}; only the first {_MAX_VLM_PAGES} are analysed.")
        pages = pages[:_MAX_VLM_PAGES]
    if diag is not None:
        diag["pages"] = len(pages)
    if not pages:
        return []

    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = _VLM_PROMPT.format(types=list(types))
    results: list[dict] = []

    for page_number, png in pages:
        b64 = base64.b64encode(png).decode("utf-8")
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]
        parsed: list = []
        raw = ""
        # Vision detection is non-deterministic: a spurious empty result on a form that
        # clearly has checkboxes is common. Retry ONCE when the first attempt parses to
        # nothing, so a fluke empty doesn't silently drop all elements for the page.
        for attempt in range(2):
            try:
                response = await llm_model.ainvoke(
                    [SystemMessage(content=_VLM_SYSTEM), HumanMessage(content=content)]
                )
                raw = response.content if hasattr(response, "content") else str(response)
                if not isinstance(raw, str):
                    # Some providers return content as a list of blocks — flatten to text.
                    raw = json.dumps(raw) if raw is not None else ""
            except Exception as e:
                logger.warning(f"Vision detection call failed on page {page_number} (attempt {attempt + 1}): {e}")
                if diag is not None:
                    diag.setdefault("errors", []).append(f"p{page_number}: {str(e)[:160]}")
                break
            parsed = _parse_json_array(raw)
            if parsed:
                break  # got results — no retry needed
            if diag is not None and attempt == 0:
                diag["retries"] = diag.get("retries", 0) + 1
        if diag is not None:
            diag["raw_items"] = diag.get("raw_items", 0) + len(parsed)
            # Capture what the model actually said when we parsed nothing, so a silent 0 is
            # diagnosable in the flow log (was it "[]", prose, a refusal, or unparseable?).
            if not parsed and (raw or "").strip():
                diag.setdefault("samples", []).append(f"p{page_number}: {raw.strip()[:200]}")
        for item in parsed:
            if not isinstance(item, dict):
                continue
            et = str(item.get("element_type") or item.get("type") or "").strip().lower()
            if et not in types:
                continue
            bbox = item.get("bounding_box") or {}
            decoded = item.get("decoded_value")
            if et == "checkbox":
                # Store label + state as JSON in decoded_value (no schema change). The frontend
                # parses it to show "<label>  [checked|unchecked]".
                raw_state = str(decoded or item.get("state") or "").strip().lower()
                state = "checked" if raw_state.startswith("check") and "un" not in raw_state else "unchecked"
                lbl = item.get("label")
                lbl = str(lbl).strip() if lbl not in (None, "") else None
                decoded_value = json.dumps({"label": lbl, "state": state})
            else:
                decoded_value = str(decoded) if decoded not in (None, "") else None
            results.append({
                "element_type": et,
                "page_number": page_number,
                "bounding_box": {
                    "x_min": _clamp(bbox.get("x_min")),
                    "y_min": _clamp(bbox.get("y_min")),
                    "x_max": _clamp(bbox.get("x_max")),
                    "y_max": _clamp(bbox.get("y_max")),
                },
                "confidence": _clamp(item.get("confidence"), default=0.7),
                "decoded_value": decoded_value,
            })

    return results


# ─────────────────────────── public API ───────────────────────────
async def detect_visual_elements(
    file_bytes: bytes,
    file_type: str,
    *,
    llm_model=None,
    wanted_types: set[str] | None = None,
    selected_pages: set[int] | None = None,
    diag: dict | None = None,
) -> list[dict]:
    """Detect visual elements in a document.

    QR codes and barcodes are decoded deterministically (pyzbar). Signatures, checkboxes,
    stamps, logos and handwritten annotations are detected by a vision LLM when ``llm_model``
    is provided. Returns a list of dicts::

        {"element_type": str, "page_number": int,
         "bounding_box": {"x_min","y_min","x_max","y_max"},
         "confidence": float, "decoded_value": str | None}
    """
    elements: list[dict] = []

    want_barcode = wanted_types is None or any(t in wanted_types for t in _BARCODE_TYPES)
    if want_barcode:
        # page rendering + pyzbar decoding are CPU-bound — run off the event loop
        elements.extend(await asyncio.to_thread(_detect_barcodes, file_bytes, file_type, selected_pages))

    if llm_model is not None:
        elements.extend(
            await _detect_via_vlm(file_bytes, file_type, llm_model, wanted_types, selected_pages, diag=diag)
        )

    return elements


async def detect_and_persist(
    session,
    document_id: UUID,
    file_bytes: bytes,
    file_type: str,
    enabled_types: set[str] | None = None,
    *,
    llm_model=None,
    selected_pages: set[int] | None = None,
    diag: dict | None = None,
    clear_existing: bool = False,
) -> list[UUID]:
    """Detect visual elements and persist them to ``idp_detected_elements``.

    Args:
        session: Active async DB session.
        document_id: The document UUID.
        file_bytes: Raw bytes of the document.
        file_type: File extension (e.g. 'pdf', 'png').
        enabled_types: Element types to keep (e.g. {'signature','checkbox','qr'}). ``None`` = keep all.
        llm_model: A vision-capable LangChain chat model for signature/checkbox/stamp/logo/handwriting.
                   When ``None``, only QR/barcode (pyzbar) detection runs.
        selected_pages: Optional 1-based page set to restrict detection to.
        clear_existing: When True, delete any prior detected elements for this document first, so a
                        reprocess REPLACES the results instead of accumulating stale rows across runs.

    Returns:
        List of created IdpDetectedElement IDs.
    """
    from sqlmodel import select
    from agentcore.services.database.models.idp.documents import IdpDetectedElement

    if clear_existing:
        prior = (
            await session.exec(
                select(IdpDetectedElement).where(IdpDetectedElement.document_id == document_id)
            )
        ).all()
        for old in prior:
            await session.delete(old)
        if prior:
            await session.commit()

    elements = await detect_visual_elements(
        file_bytes,
        file_type,
        llm_model=llm_model,
        wanted_types=enabled_types,
        selected_pages=selected_pages,
        diag=diag,
    )

    created_ids: list[UUID] = []
    for el in elements:
        element_type = el["element_type"]
        if enabled_types is not None and element_type not in enabled_types:
            continue
        db_el = IdpDetectedElement(
            id=uuid4(),
            document_id=document_id,
            element_type=element_type,
            page_number=el["page_number"],
            bounding_box=el["bounding_box"],
            confidence=el["confidence"],
            decoded_value=el["decoded_value"],
        )
        session.add(db_el)
        created_ids.append(db_el.id)

    if created_ids:
        await session.commit()

    return created_ids
