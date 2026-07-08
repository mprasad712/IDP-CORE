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
        Output(display_name="Single Document", name="single_document", method="single_document",
               group_outputs=True),
        Output(display_name="Split", name="split", method="split", group_outputs=True),
    ]

    _decided: bool = False
    _decision: tuple[str, list | None] = ("single", None)

    async def _page_inputs(self, payload: dict, doc: Any) -> tuple[dict[int, str], list[tuple[bytes, str]]]:
        """Per-page text (native for digital, OCR tokens for scanned) + page images (visual signal)."""
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
        page_images: list[tuple[bytes, str]] = []
        try:
            pdf = fitz.open(stream=file_bytes, filetype="pdf")
            for i in range(len(pdf)):
                if i not in page_texts:
                    page_texts[i] = pdf[i].get_text() or ""   # native text (digital)
                if bool(getattr(self, "use_vision", True)) and len(page_images) < _MAX_VISION_PAGES:
                    page_images.append((pdf[i].get_pixmap(dpi=120).tobytes("png"), "image/png"))
            pdf.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[split] page render failed: {exc}")
        return page_texts, page_images

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

                page_texts, page_images = await self._page_inputs(payload, doc)
                page_count = doc.page_count or (max(page_texts.keys(), default=-1) + 1)
                boundaries, seg_types = await detect_boundaries_hybrid(page_texts, page_images, self.llm, page_count)

                if not boundaries or len(boundaries) < 2:
                    self.status = "single document — passthrough"
                    self._decision = ("single", None); return self._decision

                storage = get_storage_service()
                child_ids = await materialize_children(session, storage, doc, boundaries)
                if not child_ids:
                    self.status = "split produced no children — passthrough"
                    self._decision = ("single", None)
                    return self._decision

                # pre-type each child from the boundary LLM (skip 'unknown')
                if seg_types:
                    if len(seg_types) != len(child_ids):
                        logger.debug(f"[split] seg_types({len(seg_types)}) != children({len(child_ids)}) — pairing by order")
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
                    doc.error_message = "multi-doc split: no child document could be enqueued"
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

    async def single_document(self) -> Message:
        from agentcore.services.idp.graph_native.payload import carry
        kind, _ = await self._decide()
        if kind == "single":
            return carry(self.document)
        self.stop("single_document")
        return self.document

    async def split(self) -> Message:
        from agentcore.services.idp.graph_native.payload import carry
        kind, child_ids = await self._decide()
        if kind == "split":
            return carry(self.document, split=True)
        self.stop("split")
        return self.document
