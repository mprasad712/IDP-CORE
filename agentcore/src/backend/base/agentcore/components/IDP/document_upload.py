from __future__ import annotations

from uuid import UUID

from agentcore.custom.custom_node.node import Node
from agentcore.io import MessageTextInput, Output
from agentcore.schema.message import Message


class IDPDocumentUpload(Node):
    """Entry component for a native (graph_langgraph) IDP run.

    The run injects the uploaded document's id (as ``document_id``); this loads the stored file +
    row metadata, classifies digital/scanned, extracts native text, and seeds the IDP working-set
    Message that flows through the rest of the graph.
    """

    display_name = "Document Upload"
    description = "Loads an uploaded IDP document (bytes + metadata) into the flow and classifies it."
    icon = "Upload"
    name = "IDPDocumentUpload"

    inputs = [
        MessageTextInput(
            name="document_id",
            display_name="Document",
            value="",
            # Hidden on the canvas: the document is uploaded in the Playground (or via the API), which
            # injects its id into the run. There is no manual file upload on this node.
            show=False,
            advanced=True,
            info="The uploaded document's id — set automatically by the run. Loads the stored file + metadata.",
        ),
    ]
    outputs = [Output(display_name="Document", name="document", method="load")]

    async def load(self) -> Message:
        from sqlmodel import select

        from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
        from agentcore.services.deps import get_storage_service, session_scope
        from agentcore.services.idp import text_layer
        from agentcore.services.idp.graph_native.payload import new_message

        raw = str(getattr(self, "document_id", "") or getattr(self, "input_value", "") or "").strip()
        if not raw:
            raise ValueError("Document Upload: no document_id provided to the run.")
        doc_id = UUID(raw)

        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            if doc is None:
                raise ValueError(f"IDP document {raw} not found.")
            job = (
                await session.exec(
                    select(IdpProcessingJob)
                    .where(IdpProcessingJob.document_id == doc_id)
                    .order_by(IdpProcessingJob.created_at.desc())
                )
            ).first()
            # The directory the document was ACTUALLY written into — read it from `file_path`, never
            # rebuild it from `doc.agent_id`. Documents stored before the directory rename live under the
            # old `idp_agents.id` folder and must keep resolving. See services/idp/storage_scope.py.
            from agentcore.services.idp.storage_scope import scope_of

            file_path = doc.file_path or ""
            agent_scope = scope_of(file_path)
            file_name = file_path.split("/", 1)[1] if "/" in file_path else file_path
            file_type = (doc.file_type or "").lower().lstrip(".")
            job_id = str(job.id) if job else None
            idp_agent_id = str(doc.agent_id)

        import asyncio

        file_bytes = await get_storage_service().get_file(agent_scope, file_name)
        # PDF parsing / page classification is CPU-bound — keep it off the event loop
        overall_kind, page_status = await asyncio.to_thread(
            text_layer.classify_document, file_bytes, file_type, min_text_length=50
        )

        text = ""
        tokens: list[dict] = []
        try:
            _, tokens = await asyncio.to_thread(text_layer.extract_native_text, file_bytes, file_type)
            text = " ".join(str(t.get("text", "")) for t in tokens)
        except Exception:  # scanned / non-text — OCR nodes downstream will supply text
            pass

        self.status = f"{overall_kind} · {len(page_status)} page(s)"
        return new_message(
            text=text,
            document_id=str(doc_id),
            job_id=job_id,
            # `agent_id` is the IdpAgent row id (what IdpDocument.agent_id FKs to); `agent_scope` is the
            # storage DIRECTORY. They used to be the same string — they are not any more.
            agent_id=idp_agent_id,
            agent_scope=agent_scope,
            file_name=file_name,
            file_type=file_type,
            overall_kind=overall_kind,
            page_status=page_status,
            # Carry the native token stream, not just the flattened text. It is the ONLY thing that can say
            # whether an extracted value is actually in the document. Without it every field on a digital
            # PDF persists `grounded=NULL` — unverifiable — because only the PaddleOCR node ever set this,
            # and a digital document never reaches it. A scanned page yields few or no tokens here; the OCR
            # node overwrites them downstream via `carry(...)`.
            tokens=tokens,
        )
