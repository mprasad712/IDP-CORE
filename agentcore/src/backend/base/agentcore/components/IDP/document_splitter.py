from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, HandleInput, MessageInput, Output
from agentcore.schema.message import Message

# module-level names so tests can monkeypatch them
from agentcore.services.deps import session_scope, get_storage_service
from agentcore.services.idp.splitting import detect_boundaries_hybrid, materialize_children
from agentcore.services.database.models.idp.documents import IdpDocument

_MAX_VISION_PAGES = 12  # cap page images sent to the LLM


class IDPDocumentSplitter(Node):
    display_name = "Document Splitter"
    description = (
        "Detects when one uploaded PDF actually contains several documents (invoice + delivery note + "
        "T&Cs, KYC bundles, ...) and splits it into one child document per sub-document. Each child is "
        "classified + extracted independently. Connect a model to enable smart (visual+textual) detection; "
        "without one it uses textual rules. Place it right after the Input node."
    )
    icon = "Scissors"
    name = "IDPDocumentSplitter"

    inputs = [
        MessageInput(name="document", display_name="Document", info="Document to check for splitting.",
                     required=True),
        HandleInput(name="llm", display_name="Language Model", input_types=["LanguageModel"], required=False,
                    info="Optional. A vision-capable model gives the most accurate boundary detection; "
                         "without a model, textual rules are used."),
        BoolInput(name="use_vision", display_name="Use page images", value=True, advanced=True,
                  info="Send page images to the model (visual signal) when a model is connected."),
    ]

    outputs = [
        Output(display_name="Document", name="output_document", method="route"),
    ]

    _decided: bool = False
    _decision: tuple[str, list | None] = ("single", None)

    async def _page_inputs(self, payload: dict, doc: Any) -> tuple[dict[int, str], list[tuple[int, bytes, str]], bytes]:
        """Per-page text (native for digital, OCR tokens for scanned) + page images for the LLM.

        Images are rendered ONLY for textless (image-only/scanned) pages — digital pages already
        send their text, so their images would add cost and no signal — and only when a model is
        connected with Use Vision on. Bundles with more textless pages than _MAX_VISION_PAGES send
        no images at all: at that size local OCR + a text-only call is the cheaper strategy, and
        detect_boundaries_hybrid switches to it automatically."""
        import fitz
        from agentcore.services.idp.graph_native.payload import load_bytes

        page_texts: dict[int, str] = {}
        tokens = payload.get("tokens") or []
        if tokens:  # scanned/OCR path — reconstruct per-page text from tokens
            from agentcore.services.idp.restruct import reconstruct_page
            by_page: dict[int, list] = {}
            for t in tokens:
                p = int(t.get("page_number", 1)) - 1
                by_page.setdefault(max(0, p), []).append(t)
            for p_idx, toks in by_page.items():
                page_texts[p_idx] = reconstruct_page(toks)

        file_bytes = await load_bytes(payload)
        want_vision = bool(getattr(self, "use_vision", True)) and self.llm is not None

        def _read_pdf() -> list[tuple[int, bytes, str]]:
            # CPU-bound PDF parsing + rasterization — runs in a worker thread (asyncio.to_thread)
            # so the event loop keeps serving HTTP requests while a bundle is analysed.
            images: list[tuple[int, bytes, str]] = []
            pdf = fitz.open(stream=file_bytes, filetype="pdf")
            try:
                for i in range(len(pdf)):
                    if i not in page_texts:
                        page_texts[i] = pdf[i].get_text() or ""   # native text (digital)
                textless = [i for i in range(len(pdf)) if len((page_texts.get(i) or "").strip()) < 10]
                if want_vision and 0 < len(textless) <= _MAX_VISION_PAGES:
                    for i in textless:
                        # 96 dpi JPEG: boundary/type detection needs layout + headers, not fine print
                        pix = pdf[i].get_pixmap(dpi=96)
                        images.append((i, pix.tobytes("jpeg", jpg_quality=60), "image/jpeg"))
                elif want_vision and len(textless) > _MAX_VISION_PAGES:
                    logger.info(
                        f"[split] {len(textless)} image-only pages exceed the {_MAX_VISION_PAGES}-image budget; "
                        "using local OCR + text-only detection instead"
                    )
            finally:
                pdf.close()
            return images

        import asyncio
        page_images: list[tuple[int, bytes, str]] = []
        try:
            page_images = await asyncio.to_thread(_read_pdf)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[split] page render failed: {exc}")
        return page_texts, page_images, file_bytes

    @staticmethod
    def _make_ocr_pages(file_bytes: bytes):
        """Async callback OCR-ing specific (0-based) pages of the parent PDF; {page_idx: text}.
        Used by detect_boundaries_hybrid when textless pages need text (no vision / vision failed)."""

        async def _ocr_pages(pages: list[int]) -> dict[int, str]:
            import asyncio

            import fitz
            from agentcore.services.idp.ocr import run_paddle_ocr
            from agentcore.services.idp.restruct import reconstruct_page

            def _render(p_idx: int) -> bytes | None:
                # CPU-bound rasterization — worker thread, not the event loop
                pdf = fitz.open(stream=file_bytes, filetype="pdf")
                try:
                    if not (0 <= p_idx < len(pdf)):
                        return None
                    # 200 dpi PNG: OCR needs more resolution than the LLM's visual skim
                    return pdf[p_idx].get_pixmap(dpi=200).tobytes("png")
                finally:
                    pdf.close()

            out: dict[int, str] = {}
            for p_idx in pages:
                try:
                    png = await asyncio.to_thread(_render, p_idx)
                    if png is None:
                        continue
                    tokens = await run_paddle_ocr(png, "png")
                    text = reconstruct_page(tokens) if tokens else ""
                    if text.strip():
                        out[p_idx] = text
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[split] OCR failed for page {p_idx + 1}: {exc}")
            return out

        return _ocr_pages

    async def _decide(self) -> tuple[str, list | None]:
        """Decide 'single' vs 'split' ONCE (memoized). On split: materialize children, pre-type them
        from the LLM's per-segment types, finalize the parent as a container, enqueue each child. Both
        output methods call this; side-effects run once."""
        if self._decided:
            return self._decision
        self._decided = True
        try:
            from agentcore.services.idp.graph_native.payload import effective_payload
            src = self.document
            payload = effective_payload(self, src)
            doc_id_raw = payload.get("document_id")
            if not doc_id_raw:
                self._decision = ("single", None); return self._decision
            doc_id = UUID(str(doc_id_raw))

            async with session_scope() as session:
                doc = await session.get(IdpDocument, doc_id)
                # passthrough: missing / split CHILD / non-PDF / already-split (idempotency guard)
                if (doc is None or doc.parent_document_id is not None
                        or (doc.file_type or "").lower() != "pdf" or doc.status == "split"):
                    self.status = "not splittable — passthrough"
                    self._decision = ("single", None); return self._decision

                page_texts, page_images, file_bytes = await self._page_inputs(payload, doc)
                page_count = doc.page_count or (max(page_texts.keys(), default=-1) + 1)
                boundaries, seg_types = await detect_boundaries_hybrid(
                    page_texts, page_images, self.llm, page_count,
                    ocr_pages=self._make_ocr_pages(file_bytes),
                )

                if not boundaries or len(boundaries) < 2:
                    self.status = "single document — passthrough"
                    self._decision = ("single", None); return self._decision

                storage = get_storage_service()
                from sqlmodel import select
                existing = (await session.exec(
                    select(IdpDocument).where(
                        IdpDocument.parent_document_id == doc.id,
                        IdpDocument.deleted_at.is_(None),
                    )
                )).all()
                if existing:
                    child_ids = [c.id for c in existing]   # reuse — a prior fork already created them
                    seg_types = None                       # already pre-typed on first materialize
                else:
                    child_ids = await materialize_children(session, storage, doc, boundaries)
                if not child_ids:
                    self.status = "split produced no children — passthrough"
                    self._decision = ("single", None)
                    return self._decision

                # pre-type each child from the boundary LLM (skip 'unknown')
                if seg_types:
                    if len(seg_types) != len(child_ids):
                        logger.warning(f"[split] seg_types({len(seg_types)}) != children({len(child_ids)}) — pairing by order")
                    for cid, ctype in zip(child_ids, seg_types):
                        c = await session.get(IdpDocument, cid)
                        if c is not None and ctype and str(ctype).lower() != "unknown":
                            c.predicted_type = ctype
                            session.add(c)
                await session.commit()   # persist children + pre-types (visible to enqueue_document)

                # Enqueue each child (best-effort). LAZY import of enqueue_document to avoid a module-level
                # pipeline import (see Fix 2).
                from agentcore.services.idp.pipeline import enqueue_document
                enqueued = 0
                for cid in child_ids:
                    try:
                        await enqueue_document(session, cid)
                        enqueued += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[split] could not enqueue child {cid}: {exc}")

                if enqueued == 0:
                    # SAFETY NET (mirror the fixed pipeline): no child could be enqueued -> FAIL the parent,
                    # do NOT mark it 'split' (which would strand it with zero processing children).
                    doc.status = "failed"
                    # (IdpDocument has no error_message column — it's on IdpProcessingJob.)
                    doc.failed_at = datetime.now(timezone.utc)
                    session.add(doc)
                    await session.commit()
                    self.status = "split failed — no children enqueued"
                    self._decision = ("split", child_ids)
                    return self._decision

                doc.status = "split"
                doc.processing_completed_at = datetime.now(timezone.utc)
                session.add(doc)
                await session.commit()
                self.status = f"split into {len(child_ids)} documents ({enqueued} enqueued)"
                self._decision = ("split", child_ids)
                return self._decision
        except Exception as exc:  # noqa: BLE001 — never crash the run; degrade to passthrough
            logger.warning(f"[split] decision failed, passthrough: {exc}")
            self._decision = ("single", None); return self._decision

    async def route(self) -> Message:
        from agentcore.services.idp.graph_native.payload import carry
        kind, _ = await self._decide()
        return carry(self.document, decision="split") if kind == "split" else carry(self.document)
