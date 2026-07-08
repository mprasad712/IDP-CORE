from __future__ import annotations

from uuid import UUID

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import HandleInput, Output
from agentcore.schema.data import Data


class IDPProcessedDocsOutput(Node):
    """Terminal sink for a native (graph_langgraph) IDP run.

    Reads the accumulated IDP working-set (with the extracted fields), persists the extracted
    headers/line-items via ``save_extraction_results``, and finalizes the document status. Accepts a
    list input so multiple producers (e.g. AI Field Extractor + Visual Element Detection) can fan in.
    """

    display_name = "Processed Docs Output"
    description = "Persists the extracted fields and finalizes the document (terminal node)."
    icon = "FileCheck2"
    name = "IDPProcessedDocsOutput"

    inputs = [
        HandleInput(
            name="data",
            display_name="Extracted Data",
            input_types=["Data", "Message"],
            is_list=True,
            required=False,
        ),
    ]
    outputs = [Output(display_name="Result", name="result", method="finalize")]

    async def finalize(self) -> Data:
        from agentcore.services.database.models.idp.documents import IdpDocument
        from agentcore.services.deps import session_scope
        from agentcore.services.idp.extraction import save_extraction_results
        from agentcore.services.idp.graph_native.payload import get_payload, get_shared_state

        items = self.data if isinstance(self.data, list) else [self.data]
        # Start from the shared working-set channel so document_id / extracted survive even when the
        # immediate inputs are bare Data(extracted) or a Message an upstream node stripped.
        payload: dict = dict(get_shared_state(self))
        extracted: dict = {}
        # Track whether any LOCAL input item carries a terminal decision (skip/drop).
        # The extractor that sets decision is the sink's direct upstream, so its carry(...)
        # decision always arrives on a local data item.  We gate the skip/drop short-circuit
        # on this local flag — not on the merged payload — to avoid acting on a shared-state
        # value that could (theoretically) bleed from a different document's run.
        local_terminal_decision: str = ""
        for item in items:
            p = get_payload(item)
            if p:
                payload = {**payload, **p}
                if isinstance(p.get("extracted"), dict) and (p["extracted"].get("headers") or p["extracted"].get("line_items")):
                    extracted = p["extracted"]
                # Capture terminal decision from LOCAL item payloads only.
                item_decision = str(p.get("decision") or "").strip().lower()
                if item_decision in ("skipped", "dropped") and not local_terminal_decision:
                    local_terminal_decision = item_decision
            # Also accept a plain Data(extracted) output on the edge.
            data_dict = getattr(item, "data", None)
            if isinstance(data_dict, dict) and (data_dict.get("headers") or data_dict.get("line_items")):
                extracted = data_dict
        # Fall back to the extracted block already in the working-set channel (harvested from the
        # extractor) if none of the immediate inputs carried it.
        if not extracted and isinstance(payload.get("extracted"), dict):
            ex = payload["extracted"]
            if ex.get("headers") or ex.get("line_items"):
                extracted = ex

        doc_id_raw = payload.get("document_id")
        if not doc_id_raw:
            self.status = "No document_id in the flow — nothing to persist."
            return Data(data={"status": "error", "error": "no document_id in payload"})

        doc_id = UUID(str(doc_id_raw))
        job_id_raw = payload.get("job_id")
        job_id = UUID(str(job_id_raw)) if job_id_raw else doc_id

        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            # Respect a terminal status already committed upstream (extractor skip/drop -> 'skipped';
            # Document Splitter -> 'split'/'failed'). The sink must NOT overwrite it with pending_review.
            if doc is not None and doc.status in ("skipped", "split", "failed"):
                self.status = f"Document already '{doc.status}' upstream — ProcessedDocsOutput left it unchanged."
                logger.info(f"[IDP native] {doc_id}: already '{doc.status}'; sink no-op.")
                return Data(data={"status": doc.status, "document_id": str(doc_id)})

            # Belt-and-braces: if the extractor carried a terminal decision in the IDP payload
            # (decision="skipped" or "dropped") on a LOCAL input item, honour it WITHOUT
            # re-reading the DB status.  This closes the cross-session race where a separate DB
            # session hasn't flushed yet and the DB-status guard above reads a stale non-terminal
            # value.  We gate on local_terminal_decision (set from local item payloads only) so a
            # shared-state bleed from another document's run cannot trigger a spurious skip.
            decision = str(payload.get("decision") or "").strip().lower()
            if local_terminal_decision in ("skipped", "dropped"):
                # Only overwrite status when the document is NOT already in a finalized state.
                # "reviewed" and "auto_approved" are human/system-finalized; overwriting them on a
                # re-process where the extractor now skips would clobber the human review record.
                _finalized_statuses = ("skipped", "split", "failed", "reviewed", "auto_approved")
                if doc is not None and doc.status not in _finalized_statuses:
                    doc.status = "skipped"
                    session.add(doc)
                    await session.commit()
                self.status = "Document skipped/dropped upstream — ProcessedDocsOutput left it unchanged."
                logger.info(f"[IDP native] {doc_id}: local payload decision='{local_terminal_decision}'; sink no-op.")
                return Data(data={"status": doc.status if doc is not None else "skipped", "document_id": str(doc_id)})

            overall_conf = await save_extraction_results(
                session=session,
                document_id=doc_id,
                job_id=job_id,
                extraction_result=extracted if isinstance(extracted, dict) else {},
                ocr_tokens=payload.get("tokens") or [],
                vision_mode=bool(payload.get("route") == "vision"),
            )
            # Honor the Approval Gate / router decision recorded in the working-set. The Approval Gate
            # writes decision="auto_approved" / "pending_review" into the payload; without a gate in the
            # flow, default to pending_review. All are valid IdpDocument statuses.
            if decision in ("auto_approved", "auto_approve", "approved"):
                status = "auto_approved"
            elif decision == "reviewed":
                status = "reviewed"
            else:
                status = "pending_review"
            if doc is not None:
                doc.status = status
                session.add(doc)
                await session.commit()

        n_head = len(extracted.get("headers", {})) if isinstance(extracted, dict) else 0
        self.status = f"Saved {n_head} field(s) · conf={overall_conf:.2f} · {status}"
        logger.info(f"[IDP native] processed {doc_id}: {n_head} header(s), conf={overall_conf:.2f}, status={status}")
        return Data(data={"status": status, "overall_confidence": overall_conf, "document_id": str(doc_id), "headers": n_head})
