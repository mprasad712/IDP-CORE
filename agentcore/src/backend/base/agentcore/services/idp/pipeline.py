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

import asyncio
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent, IdpAgentRule
from agentcore.services.database.models.idp.documents import (
    IdpDetectedElement,
    IdpDocument,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)
from agentcore.services.deps import (
    get_queue_service,
    get_settings_service,
    get_storage_service,
    session_scope,
)
from agentcore.services.idp import long_doc, text_layer
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
from agentcore.services.idp.rules_engine import evaluate_rules


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
    """Resolve which page numbers to process from the page-selector config.

    Always returns a NON-EMPTY set: an out-of-range request falls back to all pages
    rather than silently dropping the whole document.
    """
    total = max(1, total_pages)
    all_pages = set(range(1, total + 1))
    mode = (cfg.page_selection_mode or "all").lower()
    n = max(1, cfg.first_n_pages)
    if mode == "first_n":
        return set(range(1, min(n, total) + 1))
    if mode == "last_n":
        return set(range(max(1, total - n + 1), total + 1))
    if mode == "range":
        # A range entirely past the document -> process all (never drop everything).
        if cfg.page_range_start > total:
            return all_pages
        start = max(1, cfg.page_range_start)
        end = min(total, max(start, cfg.page_range_end))
        return set(range(start, end + 1)) or all_pages
    return all_pages


async def _maybe_preprocess(file_bytes: bytes, file_type: str, cfg, flow: "FlowLog") -> bytes:
    """Apply deskew/rotation (non-fatal) to bytes destined for OCR; returns the bytes."""
    if not (cfg.fix_skew or cfg.fix_rotation):
        return file_bytes
    try:
        if cfg.fix_skew:
            file_bytes, angle = await detect_and_correct_skew(file_bytes, file_type)
            flow.step("preprocess", "ok", f"deskew angle={angle:.2f}")
        if cfg.fix_rotation:
            file_bytes, rot = await detect_and_correct_rotation(file_bytes, file_type)
            flow.step("preprocess", "ok", f"rotation={rot}")
    except Exception as e:
        flow.step("preprocess", "warn", f"skipped ({e})")
    return file_bytes


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


async def _route(session, document_id: UUID, job_id: UUID, idp_agent_id: UUID, overall_conf: float, cfg, flow: "FlowLog") -> str:
    """Decide auto_approved vs pending_review.

    If the agent has rules (``idp_agent_rules``), run the real rules engine (B17) — it can
    reference per-field values, confidence, regex, field comparisons and visual elements.
    With no rules, fall back to the B19 default-action + confidence-threshold behaviour.
    A zero-confidence (no fields) extraction always routes to review, whatever the rules say.
    """
    rules = (
        await session.exec(
            select(IdpAgentRule)
            .where(IdpAgentRule.idp_agent_id == idp_agent_id)
            .order_by(IdpAgentRule.rule_group, IdpAgentRule.display_order)
        )
    ).all()

    if not rules:
        if overall_conf <= 0.0:
            new_status = "pending_review"
        elif cfg.default_rule_action == "auto_approve" and overall_conf >= cfg.confidence_threshold:
            new_status = "auto_approved"
        else:
            new_status = "pending_review"
        flow.step("route", "ok", f"{new_status} (no rules; threshold={cfg.confidence_threshold})")
        return new_status

    # Re-query the just-saved fields + any detected visual elements for the rule conditions.
    headers = (
        await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job_id))
    ).all()
    line_items = (
        await session.exec(select(IdpExtractedLineItem).where(IdpExtractedLineItem.job_id == job_id))
    ).all()
    detected = (
        await session.exec(select(IdpDetectedElement).where(IdpDetectedElement.document_id == document_id))
    ).all()

    try:
        decision = evaluate_rules(
            overall_confidence=overall_conf,
            headers=headers,
            line_items=line_items,
            detected_elements=detected,
            rules=rules,
            default_action=cfg.default_rule_action,
        )
        action = decision.get("action", cfg.default_rule_action)
        matched = decision.get("matched_group")
        failed = len(decision.get("failed_conditions", []))
    except Exception as e:  # pragma: no cover - rules must never crash the pipeline
        flow.step("route", "warn", f"rules engine error ({e}); falling back to review")
        return "pending_review"

    new_status = "auto_approved" if action == "auto_approve" else "pending_review"
    if overall_conf <= 0.0:
        new_status = "pending_review"
    flow.step(
        "route", "ok",
        f"{new_status} via rules (matched_group={matched}, failed={failed}, "
        f"{len(rules)} rule(s))",
    )
    return new_status


# ─────────────────────────── differentiator hooks ───────────────────────────
# Each hook is OPTIONAL, NON-FATAL and import-guarded: it is a no-op until both (a) the
# owning service module exists (Ather's modules land via branch merge) and (b) the feature
# is enabled on the agent. A differentiator raising must never fail the document.

async def _hook_split(session, storage, doc, tokens, page_status, cfg, flow: "FlowLog") -> list[UUID] | None:
    """B32 multi-doc split. Returns child document ids to enqueue (caller early-returns), else None.

    Shared seam: Ather owns detect_document_boundaries + materialize_children; the pipeline
    (Basu) owns the enqueue + early-return wiring.
    """
    if not cfg.multi_doc_split:
        return None
    # A document that is itself a split child must never be split again (it shares the
    # parent's agent, so cfg.multi_doc_split is still True). This is the real recursion guard.
    if getattr(doc, "parent_document_id", None) is not None:
        flow.step("split", "ok", "child document — split skipped (already split from a parent)")
        return None
    try:
        from agentcore.services.idp.splitting import detect_document_boundaries, materialize_children
    except ImportError:
        flow.step("split", "warn", "multi-doc split not available yet")
        return None
    try:
        boundaries = detect_document_boundaries(tokens, page_status)
        if not boundaries or len(boundaries) < 2:
            flow.step("split", "ok", "single document (no split)")
            return None
        child_ids = await materialize_children(session, storage, doc, boundaries)
        flow.step("split", "ok", f"split into {len(child_ids)} child document(s)")
        return child_ids or None
    except Exception as e:
        flow.step("split", "warn", f"skipped ({e})")
        return None


async def _hook_detect(session, document_id: UUID, file_bytes: bytes, file_type: str, cfg, flow: "FlowLog") -> None:
    """B30 visual element detection -> idp_detected_elements (used later by the rules engine)."""
    if not cfg.detect_enabled:
        return
    try:
        from agentcore.services.idp.visual_detection import detect_and_persist
    except ImportError:
        flow.step("detect_elements", "warn", "visual detection persistence not available yet")
        return
    try:
        ids = await detect_and_persist(session, document_id, file_bytes, file_type, cfg.detect_enabled_types)
        flow.step("detect_elements", "ok", f"{len(ids)} element(s) detected")
    except Exception as e:
        flow.step("detect_elements", "warn", f"skipped ({e})")


async def _hook_classify(session, document_id: UUID, base_agent, merged_text: str, cfg, flow: "FlowLog") -> None:
    """B29 document classification -> idp_document_classifications; may auto-select a Named Config.

    Mutates ``cfg`` in place (field_config_id + extraction_mode) when auto-select fires.
    """
    if not cfg.classify_enabled:
        return
    try:
        from agentcore.services.idp.classification import classify_and_persist
    except ImportError:
        flow.step("classify", "warn", "classification not available yet")
        return
    try:
        org_id = getattr(base_agent, "org_id", None)
        result = await classify_and_persist(
            session, document_id, merged_text, org_id,
            auto_select=cfg.classify_auto_select, threshold=cfg.classify_threshold,
        )
        result = result if isinstance(result, dict) else {}
        sel = result.get("selected_config_id")
        if sel and cfg.classify_auto_select:
            cfg.field_config_id = sel if isinstance(sel, UUID) else UUID(str(sel))
            cfg.extraction_mode = MODE_NAMED
            flow.step("classify", "ok",
                      f"type={result.get('predicted_type')} conf={result.get('confidence')} -> auto-selected config")
        else:
            flow.step("classify", "ok",
                      f"type={result.get('predicted_type')} conf={result.get('confidence')}")
    except Exception as e:
        flow.step("classify", "warn", f"skipped ({e})")


async def _hook_math_reconcile(extracted, llm, job, cfg, flow: "FlowLog"):
    """B31 math reconcile: fix arithmetic BEFORE save. Returns the (possibly corrected) extraction."""
    if not cfg.math_reconcile_enabled or not isinstance(extracted, dict):
        return extracted
    try:
        from agentcore.services.idp.math_reconcile import reconcile_math
    except ImportError:
        flow.step("math_reconcile", "warn", "math reconcile not available yet")
        return extracted
    try:
        res = await reconcile_math(extracted, llm, max_attempts=cfg.math_reconcile_max_attempts)
        res = res if isinstance(res, dict) else {}
        job.math_reconcile_attempts = int(res.get("attempts", 0) or 0)
        flow.step("math_reconcile", "ok",
                  f"balanced={res.get('balanced')} attempts={res.get('attempts')}")
        return res.get("extracted") or extracted
    except Exception as e:
        flow.step("math_reconcile", "warn", f"skipped ({e})")
        return extracted


async def _extract_text(session, cfg, llm, text: str) -> dict:
    """Run the configured text-mode extractor on one block of text (used per-chunk too)."""
    if cfg.extraction_mode == MODE_NAMED:
        if not cfg.field_config_id:
            raise PipelineError("named-config extraction selected but no field configuration resolved")
        return await extract_named_config(
            session=session, ocr_text=text, field_config_id=cfg.field_config_id, llm_model=llm
        )
    prompt = cfg.prompt or "Extract all key fields from this document. Return structured JSON."
    return await extract_dynamic(ocr_text=text, prompt=prompt, llm_model=llm)


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

        # 2. detect digital/scanned (per page)
        original_bytes = file_bytes
        overall_kind, page_status = text_layer.classify_document(
            original_bytes, file_type, min_text_length=cfg.min_text_length
        )
        flow.step("detect", "ok", f"{overall_kind} ({len(page_status)} page(s))", page_status=page_status)

        digital_pages = {int(p) for p, k in page_status.items() if k == text_layer.DIGITAL}
        scanned_pages = {int(p) for p, k in page_status.items() if k == text_layer.SCANNED}

        # 3+4. Read text by page kind. Native text MUST come from the ORIGINAL bytes
        # (preprocessing rasterizes a PDF and would destroy a digital page's text layer);
        # OCR runs only when scanned pages exist and never replaces native digital text.
        tokens: list[dict] = []
        if overall_kind == text_layer.DIGITAL:
            _, tokens = text_layer.extract_native_text(original_bytes, file_type)
            flow.step("native", "ok", f"native text: {len(tokens)} token(s)")
        elif overall_kind == text_layer.SCANNED:
            ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
            tokens = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
            flow.step("ocr", "ok", f"PaddleOCR {cfg.ocr_lang}: {len(tokens)} token(s)")
        else:  # mixed: native text for digital pages, OCR for scanned pages
            _, native = text_layer.extract_native_text(original_bytes, file_type)
            tokens += [t for t in native if int(t.get("page_number", 1) or 1) in digital_pages]
            ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
            ocr = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
            tokens += [t for t in ocr if int(t.get("page_number", 1) or 1) in scanned_pages]
            flow.step("ocr", "ok", f"mixed: native {len(digital_pages)} digital + OCR {len(scanned_pages)} scanned page(s)")

        # HOOK: B32 multi-doc split — operate on the WHOLE document (before page selection).
        # If the file holds several documents, materialise a child doc+job each and stop here;
        # the children are normal standalone docs that process independently.
        child_ids = await _hook_split(session, storage, doc, tokens, page_status, cfg, flow)
        if child_ids:
            for cid in child_ids:
                try:
                    await enqueue_document(session, cid)
                except Exception as e:  # pragma: no cover - one bad child must not abort the rest
                    flow.step("split", "warn", f"could not enqueue child {cid}: {e}")
            doc = await session.get(IdpDocument, document_id)
            doc.status = "split"
            doc.processing_completed_at = _utcnow()
            job.status = "completed"
            job.completed_at = _utcnow()
            job.steps_completed = flow.steps_ok
            job.log = flow.events
            session.add(doc)
            session.add(job)
            await session.commit()
            await _save_artifact(
                storage, agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
            )
            return

        # HOOK: B30 visual element detection — uses the original bytes (rasterised internally).
        await _hook_detect(session, document_id, original_bytes, file_type, cfg, flow)

        # Authoritative page count = max(classified pages, highest token page) so multi-sheet
        # office files (one classification entry, many token "pages") are NOT truncated.
        token_max = max([int(t.get("page_number", 1) or 1) for t in tokens], default=1)
        total_pages = max(len(page_status), token_max)
        doc.page_count = total_pages

        # page selection (never empty; uses the authoritative page count)
        selected = _selected_pages(total_pages, cfg)
        tokens = [t for t in tokens if int(t.get("page_number", 1) or 1) in selected]
        dropped = total_pages - len(selected)
        status = "warn" if dropped > 0 else "ok"
        detail = f"selected {sorted(selected)} of {total_pages}"
        if dropped > 0:
            detail += f" ({dropped} page(s) skipped by Page Selector)"
        flow.step("pages", status, detail)

        # merged, page-numbered, layout-reconstructed text (citation-friendly)
        merged_text = build_merged_text(tokens)
        await _save_artifact(
            storage, agent_scope, f"ocr_output/{document_id}/ocr_text.txt", merged_text.encode("utf-8")
        )
        if not merged_text.strip():
            flow.step("text", "warn", "no text extracted")

        # HOOK: B29 classification — may set cfg.predicted_type + auto-select a Named Config
        # (mutates cfg.field_config_id / cfg.extraction_mode) before extraction runs.
        await _hook_classify(session, document_id, base_agent, merged_text, cfg, flow)

        # 5. extract (text modes; multimodal parked)
        if cfg.multimodal_requested:
            flow.step("extract", "warn", "multimodal parked -> using text extraction")  # MULTIMODAL HOOK (PARKED)
        llm = await _build_llm(session, cfg.model_id)

        # HOOK: B35 long-document handling — long docs are split into section/page chunks,
        # extracted per chunk and merged; short docs take the single-pass path unchanged.
        use_long_doc = bool(cfg.long_doc_enabled) and long_doc.should_chunk(
            tokens, max_pages=cfg.long_doc_max_pages, max_tokens=cfg.long_doc_max_tokens
        )
        try:
            if use_long_doc:
                sections = long_doc.build_sections(tokens)
                try:
                    await long_doc.persist_sections(session, document_id, sections)
                except Exception as e:  # pragma: no cover - section persistence is best-effort
                    flow.step("long_doc", "warn", f"could not persist sections ({e})")
                chunk_texts = long_doc.chunks_for_extraction(
                    tokens, sections, max_tokens=cfg.long_doc_max_tokens
                )
                flow.step("long_doc", "ok", f"{len(sections)} section(s) -> {len(chunk_texts)} chunk(s)")
                results: list[dict] = []
                last_err: str | None = None
                for i, ct in enumerate(chunk_texts):
                    try:
                        res = await _extract_text(session, cfg, llm, ct)
                        # extract_dynamic swallows LLM/transport errors into {"error":...} rather
                        # than raising; surface that per chunk so a failed chunk isn't silent.
                        if isinstance(res, dict) and res.get("error"):
                            last_err = str(res.get("error"))
                            flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} error: {last_err}")
                        results.append(res)
                    except PipelineError:
                        raise
                    except Exception as e:
                        last_err = str(e)
                        flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} failed ({e})")
                # If chunking produced chunks but EVERY one failed, treat it as a fatal
                # extraction failure (consistent with the single-pass path), not a silent
                # zero-field pending_review.
                if chunk_texts and not results:
                    raise PipelineError(f"extraction failed: all {len(chunk_texts)} chunk(s) failed ({last_err})")
                extracted = long_doc.merge_chunk_extractions(results) if results else {"headers": {}, "line_items": []}
            else:
                extracted = await _extract_text(session, cfg, llm, merged_text)
        except PipelineError:
            raise
        except Exception as e:
            raise PipelineError(f"extraction failed: {e}") from e

        # Unify failure semantics across extraction modes: extract_dynamic swallows
        # LLM/transport errors into an {"error": ...} dict instead of raising. If the
        # extractor reports an error AND produced no fields, treat it as a fatal failure
        # (same terminal state as a named-config error) rather than a clean pending_review.
        if isinstance(extracted, dict) and extracted.get("error"):
            n_fields = len(extracted.get("headers", {})) + len(extracted.get("line_items", []))
            if n_fields == 0:
                raise PipelineError(f"extraction failed: {extracted.get('error')}")
            flow.step("extract", "warn", f"partial extraction: {extracted.get('error')}")

        # HOOK: B31 math reconcile — re-prompt the LLM to fix arithmetic BEFORE saving.
        extracted = await _hook_math_reconcile(extracted, llm, job, cfg, flow)

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

        # HOOK: B36 cross-page entity linking — record entities seen on multiple pages.
        if cfg.entity_linking_enabled:
            try:
                from agentcore.services.idp.entity_linking import link_entities

                n_links = await link_entities(
                    session, document_id, extracted if isinstance(extracted, dict) else {}, tokens
                )
                if n_links:
                    flow.step("entity_link", "ok", f"{n_links} cross-page entity link(s)")
            except Exception as e:  # pragma: no cover - linking must never fail the doc
                flow.step("entity_link", "warn", f"skipped ({e})")

        # 7. route — the rules engine (B17) decides auto_approve vs pending_review.
        new_status = await _route(session, document_id, job.id, idp_agent.id, overall_conf, cfg, flow)

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
    job_key = str(job.id)
    queue.create_queue(job_key)
    queue.start_job(job_key, process_document(document_id, job.id))

    # Reap the queue entry once the job finishes. JobQueueService only auto-cleans
    # cancelled/errored tasks; process_document never raises, so without this every
    # processed document would leak a queue entry forever. The monitor runs as a
    # separate task so cleanup_job (which cancels) is never called on the live job task.
    try:
        _, _, task, _ = queue.get_queue_data(job_key)
        if task is not None:
            _spawn_monitor(_reap_when_done(queue, job_key, task))
    except Exception:  # pragma: no cover - reaping is best-effort
        logger.warning(f"[pipeline] could not schedule queue reap for {job_key}")
    return job.id


# Hold strong references to detached monitor tasks so they are not GC'd mid-run.
_MONITOR_TASKS: set = set()


def _spawn_monitor(coro) -> None:
    task = asyncio.create_task(coro)
    _MONITOR_TASKS.add(task)
    task.add_done_callback(_MONITOR_TASKS.discard)


async def _reap_when_done(queue, job_key: str, job_task) -> None:
    try:
        await asyncio.wait([job_task])
    except Exception:  # pragma: no cover
        pass
    try:
        await queue.cleanup_job(job_key)  # job_task is done now -> no self-cancel
    except Exception:  # pragma: no cover
        logger.warning(f"[pipeline] queue cleanup failed for {job_key}")
