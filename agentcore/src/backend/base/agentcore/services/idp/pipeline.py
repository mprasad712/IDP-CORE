"""Per-document IDP processing pipeline (B19).

Background orchestrator that takes an uploaded document and runs it end-to-end:
load config from the agent's saved graph -> page-select -> detect digital/scanned ->
(scanned: deskew/rotate + PaddleOCR; digital: native text) -> extract via LLM ->
save extracted fields + confidence -> route to auto_approved/pending_review.

It records a readable per-document flow log (to ``IdpProcessingJob.log`` and a stored
``flow.log`` text file) and the merged page-numbered OCR text. On any fatal error the
job/document are marked ``failed`` with a message and partial results are saved.

Hybrid/config-driven: reads the proven ``services/idp/*`` functions, not the canvas node
runtime. Multimodal extraction is parked (text modes only); rules engine, multi-doc
split and classification are later tasks (hooks left below).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import (
    IdpDocument,
    IdpProcessingJob,
)
from agentcore.services.deps import (
    get_queue_service,
    get_settings_service,
    get_storage_service,
    session_scope,
)
from agentcore.services.idp import text_layer
from agentcore.services.idp.agent_config import MODE_NAMED, resolve_pipeline_config
from agentcore.services.idp.extraction import (
    extract_dynamic,
    extract_named_config,
    save_extraction_results,
)
from agentcore.services.idp.ocr import run_paddle_ocr
from agentcore.services.idp.pre_processing import (
    detect_and_correct_rotation,
    detect_and_correct_skew,
)
from agentcore.services.idp.restruct import build_merged_text


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineError(Exception):
    """Fatal, user-meaningful pipeline error (recorded in job.error_message)."""


# ─────────────────────────────── flow log ───────────────────────────────
class FlowLog:
    """Accumulates a readable per-document processing trail (file + job.log JSONB)."""

    def __init__(self, document_id: UUID, original_filename: str) -> None:
        self.document_id = document_id
        self.filename = original_filename
        self.events: list[dict] = []
        self.lines: list[str] = []

    def step(self, name: str, status: str, detail: str = "", **fields) -> None:
        ts = _utcnow().isoformat()
        evt = {"ts": ts, "step": name, "status": status, "detail": detail}
        evt.update(fields)
        self.events.append(evt)
        self.lines.append(f"[{ts[11:19]}] {status.upper():5} {name:14} {detail}")

    @property
    def steps_ok(self) -> list[str]:
        return [e["step"] for e in self.events if e["status"] in ("ok", "warn")]

    def render(self) -> str:
        head = f"Document: {self.filename}  ({self.document_id})\n" + "=" * 64
        return head + "\n" + "\n".join(self.lines) + "\n"


# ─────────────────────────────── helpers ───────────────────────────────
async def _build_llm(session, model_id: str):
    """Build a MicroserviceChatModel for a registry model UUID (validated)."""
    from agentcore.services.database.models.model_registry.model import ModelRegistry
    from agentcore.services.model_service_client import MicroserviceChatModel

    try:
        reg = await session.get(ModelRegistry, UUID(str(model_id)))
    except (ValueError, TypeError) as e:
        raise PipelineError(f"Invalid model id '{model_id}'") from e
    if reg is None:
        raise PipelineError(f"Model '{model_id}' not found in the model registry")
    if getattr(reg, "is_active", True) is False:
        raise PipelineError(f"Model '{model_id}' is inactive")

    settings = get_settings_service().settings
    return MicroserviceChatModel(
        service_url=settings.model_service_url,
        service_api_key=settings.model_service_api_key,
        registry_model_id=str(model_id),
        provider="openai",  # placeholder — the model service resolves the real provider
        model=f"idp-extract-{str(model_id)[:8]}",
    )


def _selected_pages(total_pages: int, cfg) -> set[int]:
    """Resolve which page numbers to process from the page-selector config."""
    total = max(1, total_pages)
    mode = (cfg.page_selection_mode or "all").lower()
    n = max(1, cfg.first_n_pages)
    if mode == "first_n":
        return set(range(1, min(n, total) + 1))
    if mode == "last_n":
        return set(range(max(1, total - n + 1), total + 1))
    if mode == "range":
        start = max(1, cfg.page_range_start)
        end = min(total, max(start, cfg.page_range_end))
        return set(range(start, end + 1))
    return set(range(1, total + 1))


def _split_storage_path(file_path: str) -> tuple[str, str]:
    """``"{agent_id}/{name}"`` -> ``(agent_id, name)`` for the storage service."""
    if "/" in file_path:
        agent_part, name = file_path.split("/", 1)
        return agent_part, name
    return "", file_path


async def _save_artifact(storage, agent_id: str, rel_path: str, data: bytes) -> None:
    try:
        await storage.save_file(agent_id=agent_id, file_name=rel_path, data=data)
    except Exception as e:  # pragma: no cover - artifacts are best-effort
        logger.warning(f"[pipeline] could not store artifact {rel_path}: {e}")


# ─────────────────────────────── orchestrator ───────────────────────────────
async def process_document(document_id: UUID, job_id: UUID | None = None) -> None:
    """Background entrypoint: process one document end-to-end. Never raises."""
    try:
        async with session_scope() as session:
            await _run(session, document_id, job_id)
    except Exception:
        # Last-resort guard: this runs as a detached background task, so nothing
        # may escape. Errors inside the flow are handled by _run -> _fail; this only
        # catches setup failures (session/job creation) before the main try-block.
        logger.exception(f"[pipeline] unhandled error in process_document {document_id}")


async def _run(session, document_id: UUID, job_id: UUID | None) -> None:
    t0 = time.monotonic()
    doc = await session.get(IdpDocument, document_id)
    if doc is None:
        logger.warning(f"[pipeline] document {document_id} not found; nothing to process")
        return

    flow = FlowLog(document_id, doc.original_filename)
    storage = get_storage_service()
    # Scope artifacts/reads by the document's real agent_id, not by parsing file_path.
    agent_scope = str(doc.agent_id)
    _, file_name = _split_storage_path(doc.file_path)

    # ensure a job row (created by the endpoint normally; create one for direct calls)
    job = await session.get(IdpProcessingJob, job_id) if job_id else None
    if job is None:
        job = IdpProcessingJob(id=job_id or uuid4(), document_id=document_id, agent_id=doc.agent_id, status="queued")
        session.add(job)
        await session.commit()

    try:
        # 0. idempotency — refuse if another job for this doc is already running
        running = (
            await session.exec(
                select(IdpProcessingJob).where(
                    IdpProcessingJob.document_id == document_id,
                    IdpProcessingJob.status == "running",
                    IdpProcessingJob.id != job.id,
                )
            )
        ).first()
        if running is not None:
            flow.step("load", "error", "another job already running for this document")
            await _fail(session, doc, job, flow, "another job already running for this document", touch_doc=False)
            return

        # mark running
        doc.status = "processing"
        doc.processing_started_at = _utcnow()
        job.status = "running"
        job.started_at = _utcnow()
        session.add(doc)
        session.add(job)
        await session.commit()
        flow.step("load", "ok", f"{doc.original_filename} ({doc.file_size_bytes} bytes, {doc.file_type})")

        # resolve agents + config
        idp_agent = await session.get(IdpAgent, doc.agent_id)
        if idp_agent is None:
            raise PipelineError(f"IDP agent {doc.agent_id} not found")
        base_agent = await session.get(Agent, idp_agent.agent_id)
        cfg = await resolve_pipeline_config(session, idp_agent, base_agent)

        if not cfg.model_id:
            raise PipelineError("no model configured in agent graph")

        file_bytes = await storage.get_file(agent_scope, file_name)
        file_type = (doc.file_type or "").lower().lstrip(".")

        # 1. extension gate
        if cfg.allowed_extensions and file_type not in cfg.allowed_extensions and cfg.skip_unmatched:
            raise PipelineError(f"file type '{file_type}' not allowed by this agent")

        # 2. detect digital/scanned
        overall_kind, page_status = text_layer.classify_document(
            file_bytes, file_type, min_text_length=cfg.min_text_length
        )
        doc.page_count = len(page_status)
        flow.step("detect", "ok", f"{overall_kind} ({len(page_status)} page(s))", page_status=page_status)

        is_scanned = overall_kind in ("scanned", "mixed")

        # 3. preprocess (scanned only, non-fatal)
        if is_scanned and (cfg.fix_skew or cfg.fix_rotation):
            try:
                if cfg.fix_skew:
                    file_bytes, angle = await detect_and_correct_skew(file_bytes, file_type)
                    flow.step("preprocess", "ok", f"deskew angle={angle:.2f}")
                if cfg.fix_rotation:
                    file_bytes, rot = await detect_and_correct_rotation(file_bytes, file_type)
                    flow.step("preprocess", "ok", f"rotation={rot}")
            except Exception as e:
                flow.step("preprocess", "warn", f"skipped ({e})")

        # 4. OCR (scanned) or native text (digital)
        if is_scanned:
            tokens = await run_paddle_ocr(file_bytes, file_type, cfg.ocr_lang)
            flow.step("ocr", "ok", f"PaddleOCR {cfg.ocr_lang}: {len(tokens)} token(s)")
        else:
            _, tokens = text_layer.extract_native_text(file_bytes, file_type)
            flow.step("ocr", "ok", f"native text: {len(tokens)} token(s)")

        # page selection
        total_pages = max([int(t.get("page_number", 1) or 1) for t in tokens], default=1)
        selected = _selected_pages(total_pages, cfg)
        tokens = [t for t in tokens if int(t.get("page_number", 1) or 1) in selected]
        flow.step("pages", "ok", f"selected {sorted(selected)} of {total_pages}")

        # merged, page-numbered, layout-reconstructed text (citation-friendly)
        merged_text = build_merged_text(tokens)
        await _save_artifact(
            storage, agent_scope, f"ocr_output/{document_id}/ocr_text.txt", merged_text.encode("utf-8")
        )
        if not merged_text.strip():
            flow.step("text", "warn", "no text extracted")

        # 5. extract (text modes; multimodal parked)
        if cfg.multimodal_requested:
            flow.step("extract", "warn", "multimodal parked -> using text extraction")  # MULTIMODAL HOOK (PARKED)
        llm = await _build_llm(session, cfg.model_id)
        try:
            if cfg.extraction_mode == MODE_NAMED:
                if not cfg.field_config_id:
                    raise PipelineError("named-config extraction selected but no field configuration resolved")
                extracted = await extract_named_config(
                    session=session, ocr_text=merged_text, field_config_id=cfg.field_config_id, llm_model=llm
                )
            else:
                prompt = cfg.prompt or "Extract all key fields from this document. Return structured JSON."
                extracted = await extract_dynamic(ocr_text=merged_text, prompt=prompt, llm_model=llm)
        except PipelineError:
            raise
        except Exception as e:
            raise PipelineError(f"extraction failed: {e}") from e

        if isinstance(extracted, dict) and extracted.get("error"):
            flow.step("extract", "warn", f"extractor reported: {extracted.get('error')}")

        # 6. save. NOTE: save_extraction_results commits internally, so extracted rows
        # persist even if a later step fails — intentional partial-result persistence
        # (the failure path also saves partials; the HITL review flow handles partials).
        overall_conf = await save_extraction_results(
            session=session,
            document_id=document_id,
            job_id=job.id,
            extraction_result=extracted if isinstance(extracted, dict) else {},
            ocr_tokens=tokens,
        )
        n_head = len(extracted.get("headers", {})) if isinstance(extracted, dict) else 0
        n_line = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0
        job.extraction_mode_used = cfg.extraction_mode
        flow.step("extract", "ok", f"mode={cfg.extraction_mode} headers={n_head} lines={n_line} conf={overall_conf:.2f}")

        # 7. route (rules engine = later task; use default_rule_action + threshold)  # RULES ENGINE HOOK
        if overall_conf <= 0.0:
            new_status = "pending_review"
        elif cfg.default_rule_action == "auto_approve" and overall_conf >= cfg.confidence_threshold:
            new_status = "auto_approved"
        else:
            new_status = "pending_review"
        flow.step("route", "ok", f"{new_status} (threshold={cfg.confidence_threshold})")

        # 8. finalize
        doc = await session.get(IdpDocument, document_id)  # refresh (save committed)
        doc.status = new_status
        doc.processing_completed_at = _utcnow()
        ms = int((time.monotonic() - t0) * 1000)
        flow.step("finalize", "ok", f"completed in {ms} ms")
        job.status = "completed"
        job.completed_at = _utcnow()
        job.processing_time_ms = ms
        job.steps_completed = flow.steps_ok
        job.log = flow.events
        session.add(doc)
        session.add(job)
        await session.commit()
        await _save_artifact(
            storage, agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
        )

    except Exception as e:
        message = str(e)
        if not isinstance(e, PipelineError):
            logger.exception(f"[pipeline] unexpected error processing {document_id}")
        flow.step("fatal", "error", message)
        await _fail(session, doc, job, flow, message)


def _apply_failure(doc, job, flow: FlowLog, message: str, *, touch_doc: bool) -> None:
    if touch_doc and doc is not None:
        doc.status = "failed"
        doc.failed_at = _utcnow()
    if job is not None:
        job.status = "failed"
        job.error_message = message[:2000]
        job.completed_at = _utcnow()
        job.steps_completed = flow.steps_ok
        job.log = flow.events


async def _fail(session, doc, job, flow: FlowLog, message: str, *, touch_doc: bool = True) -> None:
    """Record a failed run (best-effort, with its own rollback guard)."""
    doc_id = doc.id if doc is not None else None
    job_id = job.id if job is not None else None
    try:
        _apply_failure(doc, job, flow, message, touch_doc=touch_doc)
        if doc is not None:
            session.add(doc)
        if job is not None:
            session.add(job)
        await session.commit()
    except Exception:
        logger.exception("[pipeline] failed to persist failure state; retrying after rollback")
        await session.rollback()
        try:
            # re-fetch by PK and reapply the COMPLETE failure state (don't drop fields)
            doc2 = await session.get(IdpDocument, doc_id) if doc_id else None
            job2 = await session.get(IdpProcessingJob, job_id) if job_id else None
            _apply_failure(doc2, job2, flow, message, touch_doc=touch_doc)
            if doc2 is not None:
                session.add(doc2)
            if job2 is not None:
                session.add(job2)
            await session.commit()
        except Exception:
            logger.exception("[pipeline] could not record failure state")
    # best-effort flow.log artifact
    try:
        if doc is not None:
            await get_storage_service().save_file(
                agent_id=str(doc.agent_id),
                file_name=f"flow_logs/{doc.id}/flow.log",
                data=flow.render().encode("utf-8"),
            )
    except Exception:  # pragma: no cover
        pass


# ─────────────────────────────── enqueue ───────────────────────────────
async def enqueue_document(session, document_id: UUID) -> UUID:
    """Create a queued job and start the background pipeline. Returns the job id.

    Raises PipelineError if a job for this document is already queued/running.
    """
    # Lock the document row so two concurrent enqueues for the SAME document serialize
    # (the second waits, then sees the first's queued/running job and is rejected).
    doc = (
        await session.exec(
            select(IdpDocument).where(IdpDocument.id == document_id).with_for_update()
        )
    ).first()
    if doc is None:
        raise PipelineError(f"document {document_id} not found")

    existing = (
        await session.exec(
            select(IdpProcessingJob).where(
                IdpProcessingJob.document_id == document_id,
                IdpProcessingJob.status.in_(("queued", "running")),
            )
        )
    ).first()
    if existing is not None:
        raise PipelineError("a processing job for this document is already queued or running")

    job = IdpProcessingJob(document_id=document_id, agent_id=doc.agent_id, status="queued")
    session.add(job)
    await session.commit()
    await session.refresh(job)

    queue = get_queue_service()
    queue.create_queue(str(job.id))
    queue.start_job(str(job.id), process_document(document_id, job.id))
    return job.id
