"""Independent backbone for the graph-driven IDP pipeline (flag-ON path).

``run_graph_pipeline`` runs an agent's canvas nodes in graph order behind the
``IDP_GRAPH_EXECUTION`` flag. It is a *separate* execution path from the legacy
``services.idp.pipeline._run`` (which is untouched): it REUSES the proven standalone step
functions (``_maybe_preprocess``, ``_hook_split``, ``_hook_detect``, ``_hook_math_reconcile``,
``_hook_sap_matching``, ``_extract_text``, ``_route``, ``_match_route``,
``save_extraction_results``, ``link_entities``, ``_maybe_webhook`` …) and re-implements only the
small inline "backbone" bits — digital/scanned detect, text acquisition, extract orchestration,
and the split/skipped/finalize terminals — as faithful COPIES of the corresponding ``_run``
blocks so the two paths reach parity (verified in Task 8).

The copied backbone blocks are the local helpers :func:`_detect` (``_run`` l.999-1014),
:func:`_acquire_text` (RAW native/OCR/mixed text acquisition — the first half of ``_run``
l.1016-1123), :func:`_select_and_merge` (page selection + merged-text build — the second half,
``_run`` l.1172-1213) and :func:`_extract_fields` (``_run`` l.1233-1399). Everything else is
delegated to the imported standalone functions.

Ordering note (parity with legacy): the multi-doc split hook (:func:`_hook_split`) runs on the
FULL (pre-page-selection) token set produced by :func:`_acquire_text`, BEFORE page selection /
merged-text build (:func:`_select_and_merge`) — exactly as legacy ``_run`` (split on the whole
document at l.1128, page selection at l.1179). This is why text acquisition is split into two
helpers with ``_hook_split`` wedged between them.

Anchoring note (parity for BOTH node-based AND extra-flag agents): classification, **Visual
Element Detection** and **Math Reconcile** run as anchored, ``cfg``-gated steps at their legacy
``_run`` positions — detect BEFORE classify (``_run`` l.1168), classify BEFORE extract
(``_run`` l.1221), math AFTER extract (``_run`` l.1428). Each is gated on the corresponding
``cfg.*_enabled`` flag, which ``agent_config`` sets true when a canvas node exists OR
``idp_agent.extra`` sets the flag. Their canvas nodes are pure graph-order MARKERS (a
``flow.step`` only); the anchored steps do the real work. Net effect: a feature enabled via
``extra.*_enabled`` with no node still runs (D-2 fix), and a classify-SKIPPED doc still gets
detection because detect precedes the skip check (D-1 fix) — exactly as legacy.
"""

from __future__ import annotations

import time
from uuid import UUID

from loguru import logger
from sqlmodel import select

from agentcore.services.database.models.idp.documents import IdpDocument, IdpExtractedHeader
from agentcore.services.database.models.model_registry.model import ModelRegistry

# Reuse the proven standalone step functions. Top-level imports are safe: ``pipeline`` does not
# import ``graph_exec`` (no cycle), and binding these names on THIS module makes them
# monkeypatchable as ``runner.<name>`` in tests — ``run_graph_pipeline`` refers to them by bare
# name so the patch takes effect.
from agentcore.services.idp import long_doc, text_layer
from agentcore.services.idp.agent_config import MODE_NAMED
from agentcore.services.idp.extraction import (
    decide_extraction_input,
    extract_vision,
    render_document_images,
    save_extraction_results,
)
from agentcore.services.idp.graph_exec.context import PipelineContext
from agentcore.services.idp.graph_exec.planner import (
    ExecutionPlan,
    prune_confidence_branches,
    prune_presence_branches,
)
from agentcore.services.idp.idp_tracing import end_idp_trace, end_span, start_span
from agentcore.services.idp.pipeline import (
    PipelineError,
    _build_llm,
    _clip,
    _extract_text,
    _hook_classify,  # anchored classify step (cfg-gated) — bare name so tests can patch runner.<name>
    _hook_detect,  # anchored visual-element detection step (cfg-gated)
    _hook_math_reconcile,  # anchored math-reconcile step (cfg-gated)
    _hook_sap_matching,
    _hook_split,
    _io_headers,
    _io_sample_rows,
    _maybe_preprocess,
    _maybe_webhook,
    _match_route,
    _named_config_vision_messages,
    _route,
    _save_artifact,
    _selected_pages,
    _split_storage_path,
    _supports_vision,
    _utcnow,
    _vision_error_hint,
    build_merged_text,
    enqueue_document,
    run_paddle_ocr,
)

__all__ = ["run_graph_pipeline"]


# ─────────────────────────── backbone block 1: detect (copy of _run l.999-1014) ───────────────


async def _detect(ctx: PipelineContext) -> None:
    """Detect digital/scanned page kind. Faithful copy of ``pipeline._run`` l.999-1014.

    Sets ``ctx.original_bytes``, ``ctx.overall_kind``, ``ctx.page_status`` and the
    ``ctx.digital_pages`` / ``ctx.scanned_pages`` sets.
    """
    document_id = ctx.document_id
    cfg = ctx.cfg
    flow = ctx.flow
    trace_ctx = ctx.trace_ctx

    logger.info(f"[pipeline] {document_id}: classifying document (digital/scanned)")
    original_bytes = ctx.file_bytes
    file_type = ctx.file_type
    if trace_ctx:
        start_span(trace_ctx, "detect_page_kind", inputs={"file_type": file_type, "size_bytes": len(original_bytes)})
    overall_kind, page_status = text_layer.classify_document(
        original_bytes, file_type, min_text_length=cfg.min_text_length
    )
    if trace_ctx:
        end_span(trace_ctx, "detect_page_kind", outputs={"overall_kind": overall_kind, "pages": len(page_status)})

    flow.step("detect", "ok", f"{overall_kind} ({len(page_status)} page(s))", page_status=page_status)
    logger.info(f"[pipeline] {document_id}: detected as {overall_kind} ({len(page_status)} pages)")

    digital_pages = {int(p) for p, k in page_status.items() if k == text_layer.DIGITAL}
    scanned_pages = {int(p) for p, k in page_status.items() if k == text_layer.SCANNED}

    ctx.original_bytes = original_bytes
    ctx.overall_kind = overall_kind
    ctx.page_status = page_status
    ctx.digital_pages = digital_pages
    ctx.scanned_pages = scanned_pages


# ───────────── backbone block 2: text acquisition (copy of _run l.1016-1189) ─────────────


async def _acquire_text(ctx: PipelineContext) -> None:
    """Extraction-route decision + RAW native/OCR/mixed text acquisition (NO page selection).

    Faithful copy of the first half of ``pipeline._run`` l.1016-1123, honoring ``cfg.input_mode``
    / ``cfg.has_ocr_node`` / vision route and the ``_maybe_preprocess`` call sites. Sets
    ``ctx.route`` (stashed for extract) and ``ctx.tokens`` (the FULL, pre-page-selection set).

    Parity note: page selection + merged-text build live in the sibling :func:`_select_and_merge`
    so the multi-doc split hook (``_hook_split``) can run on the FULL token set BETWEEN the two —
    exactly as legacy (split at l.1128, page selection at l.1179). Do NOT do page selection here.
    """
    session = ctx.session
    cfg = ctx.cfg
    flow = ctx.flow
    trace_ctx = ctx.trace_ctx
    document_id = ctx.document_id
    original_bytes = ctx.original_bytes
    file_type = ctx.file_type
    overall_kind = ctx.overall_kind
    digital_pages = ctx.digital_pages
    scanned_pages = ctx.scanned_pages
    _, file_name = _split_storage_path(ctx.doc.file_path)

    # Decide the extraction ROUTE now — BEFORE any OCR — from the agent's Input Mode + the
    # detected kind + the model's vision capability. 'vision' skips OCR entirely.
    _route_reg = None
    if cfg.model_id:
        try:
            _route_reg = await session.get(ModelRegistry, UUID(str(cfg.model_id)))
        except (ValueError, TypeError):
            _route_reg = None
    route = decide_extraction_input(cfg.input_mode, overall_kind, _supports_vision(_route_reg), cfg.has_ocr_node)
    if cfg.input_mode == "text_vision" and route == "vision":
        flow.step("input", "warn", "text+vision requested but the document has no native/OCR text — using vision only")
    logger.info(f"[pipeline] {document_id}: extraction route={route} (input_mode={cfg.input_mode}, kind={overall_kind})")
    ctx.route = route

    # Read text by page kind. Native text MUST come from the ORIGINAL bytes; OCR runs only when
    # scanned pages exist and never replaces native digital text.
    tokens: list[dict] = []
    if route == "vision":
        flow.step("ocr", "ok", "vision route — OCR skipped (model reads page images directly)")
    elif overall_kind == text_layer.DIGITAL:
        if trace_ctx:
            start_span(trace_ctx, "extract_native_text", inputs={"file_type": file_type})
        _, tokens = text_layer.extract_native_text(original_bytes, file_type)
        if trace_ctx:
            end_span(trace_ctx, "extract_native_text", outputs={"token_count": len(tokens)})

        _chars = sum(len(str(t.get("text", ""))) for t in tokens)
        flow.step("native", "ok", f"digital — native text layer used (no OCR): {len(tokens)} token(s), {_chars} chars",
                  io={"output": {"char_count": _chars, "token_count": len(tokens),
                                 "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})
    elif overall_kind == text_layer.SCANNED:
        if trace_ctx:
            start_span(trace_ctx, "preprocessing", inputs={"file_type": file_type})
        ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
        if trace_ctx:
            end_span(trace_ctx, "preprocessing", outputs={"bytes_length": len(ocr_bytes)})
        if ocr_bytes is not original_bytes:
            await _save_artifact(ctx.storage, ctx.agent_scope, file_name, ocr_bytes)
            logger.info(f"[pipeline] {document_id}: corrected file saved back to storage for preview")

        if trace_ctx:
            start_span(trace_ctx, "ocr", inputs={"ocr_lang": cfg.ocr_lang})
        tokens = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
        if trace_ctx:
            end_span(trace_ctx, "ocr", outputs={"token_count": len(tokens)})

        _chars = sum(len(str(t.get("text", ""))) for t in tokens)
        flow.step("ocr", "ok", f"PaddleOCR ({cfg.ocr_lang}) output: {len(tokens)} token(s), {_chars} chars",
                  io={"output": {"char_count": _chars, "token_count": len(tokens),
                                 "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})
    else:  # mixed: native text for digital pages, OCR for scanned pages
        if trace_ctx:
            start_span(trace_ctx, "extract_native_text", inputs={"file_type": file_type})
        _, native = text_layer.extract_native_text(original_bytes, file_type)
        if trace_ctx:
            end_span(trace_ctx, "extract_native_text", outputs={"token_count": len(native)})

        tokens += [t for t in native if int(t.get("page_number", 1) or 1) in digital_pages]

        if trace_ctx:
            start_span(trace_ctx, "preprocessing", inputs={"file_type": file_type})
        ocr_bytes = await _maybe_preprocess(original_bytes, file_type, cfg, flow)
        if trace_ctx:
            end_span(trace_ctx, "preprocessing", outputs={"bytes_length": len(ocr_bytes)})
        if ocr_bytes is not original_bytes:
            await _save_artifact(ctx.storage, ctx.agent_scope, file_name, ocr_bytes)
            logger.info(f"[pipeline] {document_id}: corrected file saved back to storage for preview")

        if trace_ctx:
            start_span(trace_ctx, "ocr", inputs={"ocr_lang": cfg.ocr_lang})
        ocr = await run_paddle_ocr(ocr_bytes, file_type, cfg.ocr_lang)
        if trace_ctx:
            end_span(trace_ctx, "ocr", outputs={"token_count": len(ocr)})

        tokens += [t for t in ocr if int(t.get("page_number", 1) or 1) in scanned_pages]
        flow.step("ocr", "ok", f"mixed: native {len(digital_pages)} digital + OCR {len(scanned_pages)} scanned page(s)",
                  io={"output": {"token_count": len(tokens),
                                 "text_sample": _clip(" ".join(str(t.get("text", "")) for t in tokens))}})

    ctx.tokens = tokens


# ───────────── backbone block 2b: page selection + merge (copy of _run l.1172-1213) ─────────────


async def _select_and_merge(ctx: PipelineContext) -> None:
    """Page selection + merged, page-numbered, layout-reconstructed text build.

    Faithful copy of ``pipeline._run`` l.1172-1213 (the second half of text acquisition, AFTER the
    multi-doc split hook). Reads the FULL ``ctx.tokens`` set from :func:`_acquire_text`, sets
    ``ctx.doc.page_count``, filters ``ctx.tokens`` to the selected pages, then sets
    ``ctx.merged_text`` and ``ctx.selected`` (page numbers, stashed for the vision render).
    """
    cfg = ctx.cfg
    flow = ctx.flow
    trace_ctx = ctx.trace_ctx
    document_id = ctx.document_id
    tokens = ctx.tokens

    # Authoritative page count = max(classified pages, highest token page).
    token_max = max([int(t.get("page_number", 1) or 1) for t in tokens], default=1)
    total_pages = max(len(ctx.page_status), token_max)
    ctx.doc.page_count = total_pages

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
    if trace_ctx:
        start_span(trace_ctx, "text_merge", inputs={"token_count": len(tokens)})
    merged_text = build_merged_text(tokens)
    if trace_ctx:
        end_span(trace_ctx, "text_merge", outputs={"char_count": len(merged_text)})

    await _save_artifact(
        ctx.storage, ctx.agent_scope, f"ocr_output/{document_id}/ocr_text.txt", merged_text.encode("utf-8")
    )
    if merged_text.strip():
        flow.step(
            "text", "ok",
            f"merged page-numbered text built: {len(merged_text)} chars across {len(selected)} page(s) "
            f"→ saved to ocr_output/{document_id}/ocr_text.txt",
            io={"output": {
                "chars": len(merged_text),
                "pages": len(selected),
                "path": f"ocr_output/{document_id}/ocr_text.txt",
                "text_sample": _clip(merged_text),
            }},
        )
    else:
        flow.step("text", "warn", "no text extracted from the document")

    ctx.tokens = tokens
    ctx.merged_text = merged_text
    ctx.selected = selected


# ───────────── backbone block 3: extraction (copy of _run l.1233-1399) ─────────────


async def _extract_fields(ctx: PipelineContext) -> None:
    """LLM extraction: vision route / text route / long-doc chunking.

    Faithful copy of ``pipeline._run`` l.1233-1399. Math reconcile (l.1401-1408) is NOT here —
    it is the Math Reconcile ``post_extract`` handler. Sets ``ctx.extracted``, ``ctx.llm`` and
    stashes ``ctx.vision_used`` / ``ctx.page_images`` for the downstream save / reconcile steps.
    """
    session = ctx.session
    cfg = ctx.cfg
    flow = ctx.flow
    trace_ctx = ctx.trace_ctx
    document_id = ctx.document_id
    route = getattr(ctx, "route", "text")
    merged_text = ctx.merged_text
    tokens = ctx.tokens
    original_bytes = ctx.original_bytes
    file_type = ctx.file_type
    selected = getattr(ctx, "selected", None)

    logger.info(f"[pipeline] {document_id}: starting extraction (mode={cfg.extraction_mode}, route={route})")
    if cfg.multimodal_requested and route in ("text", "text_vision"):
        flow.step("extract", "warn", "legacy multimodal flag → using text extraction (set Input Mode = Vision for the vision path)")
    llm = await _build_llm(session, cfg.model_id)
    vision_used = route in ("vision", "text_vision")
    _rmid = getattr(llm, "registry_model_id", None)
    page_images = None  # set on the vision path below; kept None for text routes

    use_long_doc = route in ("text", "text_vision") and bool(cfg.long_doc_enabled) and long_doc.should_chunk(
        tokens, max_pages=cfg.long_doc_max_pages, max_tokens=cfg.long_doc_max_tokens
    )
    try:
        if route in ("vision", "text_vision"):
            page_images = render_document_images(original_bytes, file_type, selected)
            if not page_images:
                raise PipelineError("vision mode: could not render any page images from the document")
            _payload_kb = sum(len(b) for b, _ in page_images) // 1024
            logger.info(
                f"[pipeline] {document_id}: vision extraction — {len(page_images)} page image(s), ~{_payload_kb} KB payload"
            )
            if trace_ctx:
                start_span(
                    trace_ctx, "extraction",
                    inputs={"page_images": len(page_images), "payload_kb": _payload_kb, "vision": True},
                    observation_type="generation",
                )
            try:
                if cfg.extraction_mode == MODE_NAMED and cfg.field_config_id:
                    fc_msgs = await _named_config_vision_messages(session, cfg.field_config_id)
                    extracted = await extract_vision(
                        page_images, llm_model=llm, field_config_messages=fc_msgs,
                        ocr_text=(merged_text if route == "text_vision" else None),
                    )
                else:
                    v_prompt = cfg.prompt or "Extract all key fields from this document. Return structured JSON."
                    extracted = await extract_vision(
                        page_images, llm_model=llm, prompt=v_prompt,
                        ocr_text=(merged_text if route == "text_vision" else None),
                    )
            except PipelineError:
                raise
            except Exception as ve:
                raise PipelineError(f"vision extraction failed{_vision_error_hint(str(ve))}: {ve}") from ve
            usage = None
            model = None
            if isinstance(extracted, dict) and "_usage" in extracted:
                usage = extracted.pop("_usage")
                if usage:
                    model = usage.get("model")
            if trace_ctx:
                end_span(trace_ctx, "extraction", outputs=extracted, usage=usage, model=model)
            flow.step("extract", "ok",
                      f"vision extraction — {len(page_images)} page image(s) (~{_payload_kb} KB) read by the model")
        elif use_long_doc:
            sections = long_doc.build_sections(tokens)
            try:
                await long_doc.persist_sections(session, document_id, sections)
            except Exception as e:  # pragma: no cover - section persistence is best-effort
                flow.step("long_doc", "warn", f"could not persist sections ({e})")
            chunk_texts = long_doc.chunks_for_extraction(
                tokens,
                sections,
                max_tokens=cfg.long_doc_max_tokens,
                overlap_tokens=cfg.long_doc_overlap_tokens,
            )
            flow.step("long_doc", "ok", f"{len(sections)} section(s) -> {len(chunk_texts)} chunk(s)")

            if trace_ctx:
                start_span(trace_ctx, "extraction", inputs={"chunk_count": len(chunk_texts), "long_doc": True}, observation_type="generation", registry_model_id=_rmid)

            results: list[dict] = []
            last_err: str | None = None
            aggregated_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            models_used = set()

            for i, ct in enumerate(chunk_texts):
                if trace_ctx:
                    start_span(trace_ctx, f"extraction_chunk_{i+1}", inputs={"chunk_index": i}, observation_type="generation", registry_model_id=_rmid)
                try:
                    res = await _extract_text(session, cfg, llm, ct)

                    usage = None
                    model = None
                    if isinstance(res, dict) and "_usage" in res:
                        usage = res.pop("_usage")
                        if usage:
                            model = usage.get("model")
                            aggregated_usage["input_tokens"] += usage.get("input_tokens", 0)
                            aggregated_usage["output_tokens"] += usage.get("output_tokens", 0)
                            aggregated_usage["total_tokens"] += usage.get("total_tokens", 0)
                            if model:
                                models_used.add(model)

                    if trace_ctx:
                        end_span(trace_ctx, f"extraction_chunk_{i+1}", outputs=res, usage=usage, model=model, registry_model_id=_rmid)

                    if isinstance(res, dict) and res.get("error"):
                        last_err = str(res.get("error"))
                        flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} error: {last_err}")
                    results.append(res)
                except PipelineError:
                    if trace_ctx:
                        end_span(trace_ctx, f"extraction_chunk_{i+1}", error="PipelineError", registry_model_id=_rmid)
                    raise
                except Exception as e:
                    if trace_ctx:
                        end_span(trace_ctx, f"extraction_chunk_{i+1}", error=str(e), registry_model_id=_rmid)
                    last_err = str(e)
                    flow.step("long_doc", "warn", f"chunk {i + 1}/{len(chunk_texts)} failed ({e})")
            if chunk_texts and not results:
                raise PipelineError(f"extraction failed: all {len(chunk_texts)} chunk(s) failed ({last_err})")
            extracted = (
                long_doc.merge_chunk_extractions(results, strategy=cfg.dedup_strategy)
                if results
                else {"headers": {}, "line_items": []}
            )
            if trace_ctx:
                primary_model = list(models_used)[0] if models_used else None
                end_span(
                    trace_ctx, "extraction",
                    outputs=extracted,
                    usage=aggregated_usage if aggregated_usage["total_tokens"] > 0 else None,
                    model=primary_model,
                    registry_model_id=_rmid
                )
        else:
            if trace_ctx:
                start_span(trace_ctx, "extraction", inputs={"text_length": len(merged_text), "long_doc": False}, observation_type="generation", registry_model_id=_rmid)

            extracted = await _extract_text(session, cfg, llm, merged_text)

            usage = None
            model = None
            if isinstance(extracted, dict) and "_usage" in extracted:
                usage = extracted.pop("_usage")
                if usage:
                    model = usage.get("model")

            if trace_ctx:
                end_span(trace_ctx, "extraction", outputs=extracted, usage=usage, model=model, registry_model_id=_rmid)
    except PipelineError:
        if trace_ctx:
            end_span(trace_ctx, "extraction", error="PipelineError", registry_model_id=_rmid)
        raise
    except Exception as e:
        if trace_ctx:
            end_span(trace_ctx, "extraction", error=str(e), registry_model_id=_rmid)
        raise PipelineError(f"extraction failed: {e}") from e

    # Unify failure semantics across extraction modes (copy of _run l.1391-1399).
    if isinstance(extracted, dict) and extracted.get("error"):
        n_fields = len(extracted.get("headers", {})) + len(extracted.get("line_items", []))
        if n_fields == 0:
            raise PipelineError(f"extraction failed: {extracted.get('error')}")
        flow.step("extract", "warn", f"partial extraction: {extracted.get('error')}")

    ctx.llm = llm
    ctx.vision_used = vision_used
    ctx.page_images = page_images if vision_used else None
    ctx.extracted = extracted


# ─────────────────────────── terminal backbone helpers ───────────────────────────


async def _finalize_split(ctx: PipelineContext) -> None:
    """Split terminal: enqueue each child doc, mark the parent 'split', notify + end trace.

    Faithful copy of ``pipeline._run`` l.1105-1139 (the split branch after ``_hook_split``).
    """
    session = ctx.session
    flow = ctx.flow
    document_id = ctx.document_id
    job = ctx.job
    cfg = ctx.cfg
    child_ids = ctx.child_ids

    enqueued_ok = 0
    for cid in child_ids:
        try:
            await enqueue_document(session, cid)
            enqueued_ok += 1
        except Exception as e:  # one bad child must not abort the rest
            flow.step("split", "warn", f"could not enqueue child {cid}: {e}")
    if enqueued_ok == 0:
        raise PipelineError(
            f"multi-doc split produced {len(child_ids)} child document(s) but none could be enqueued"
        )
    if enqueued_ok < len(child_ids):
        flow.step("split", "warn", f"only {enqueued_ok}/{len(child_ids)} child document(s) enqueued")
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
        ctx.storage, ctx.agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
    )
    await _maybe_webhook(cfg, document_id, job.id, {}, "skipped", flow)
    if ctx.trace_ctx:
        await end_idp_trace(ctx.trace_ctx, outputs={"status": "split", "child_ids": [str(cid) for cid in child_ids]})


async def _finalize_skipped(ctx: PipelineContext) -> None:
    """Classification-skip terminal. Faithful copy of ``pipeline._run`` l.1216-1230."""
    doc = ctx.doc
    job = ctx.job
    flow = ctx.flow

    doc.status = "skipped"
    doc.processing_completed_at = _utcnow()
    job.status = "completed"
    job.completed_at = _utcnow()
    job.steps_completed = flow.steps_ok
    job.log = flow.events
    ctx.session.add(doc)
    ctx.session.add(job)
    await ctx.session.commit()
    await _maybe_webhook(ctx.cfg, ctx.document_id, job.id, {}, "skipped", flow)
    if ctx.trace_ctx:
        await end_idp_trace(ctx.trace_ctx, outputs={"status": "skipped"})


async def _save_and_link(ctx: PipelineContext) -> None:
    """Persist extracted rows + confidence, then cross-page entity linking.

    Faithful copy of ``pipeline._run`` l.1426-1484. Sets ``ctx.overall_conf``.
    """
    session = ctx.session
    cfg = ctx.cfg
    flow = ctx.flow
    trace_ctx = ctx.trace_ctx
    document_id = ctx.document_id
    job = ctx.job
    extracted = ctx.extracted
    tokens = ctx.tokens
    merged_text = ctx.merged_text

    _headers = extracted.get("headers", {}) if isinstance(extracted, dict) else {}
    n_head = len(_headers)
    n_line = len(extracted.get("line_items", [])) if isinstance(extracted, dict) else 0

    if trace_ctx:
        start_span(trace_ctx, "save_results", inputs={"n_headers": n_head, "n_line_items": n_line})
    overall_conf = await save_extraction_results(
        session=session,
        document_id=document_id,
        job_id=job.id,
        extraction_result=extracted if isinstance(extracted, dict) else {},
        ocr_tokens=tokens,
        vision_mode=getattr(ctx, "vision_used", False),
    )
    if trace_ctx:
        end_span(trace_ctx, "save_results", outputs={"overall_confidence": overall_conf})

    _names = list(_headers.keys())
    _preview = ", ".join(_names[:12]) + ("…" if len(_names) > 12 else "")
    job.extraction_mode_used = cfg.extraction_mode
    _ex = extracted if isinstance(extracted, dict) else {}
    flow.step(
        "extract", "ok",
        f"LLM ({cfg.extraction_mode}) returned {n_head} header field(s) [{_preview}] "
        f"+ {n_line} line-item row(s); overall confidence={overall_conf:.2f} → saved to "
        f"idp_extracted_headers/idp_extracted_line_items",
        io={
            "input": {"mode": cfg.extraction_mode, "config_name": cfg.config_name,
                      "field_config_id": str(cfg.field_config_id) if cfg.field_config_id else None,
                      "chars": len(merged_text), "text_sample": _clip(merged_text)},
            "output": {
                "headers": _io_headers(_ex),
                "line_item_rows": n_line,
                "sample_rows": _io_sample_rows(_ex),
                "overall_confidence": round(float(overall_conf), 4),
            },
        },
    )

    ctx.overall_conf = overall_conf

    # HOOK: B36 cross-page entity linking — record entities seen on multiple pages.
    if cfg.entity_linking_enabled:
        try:
            from agentcore.services.idp.entity_linking import link_entities
            if trace_ctx:
                start_span(trace_ctx, "entity_linking")
            n_links = await link_entities(
                session, document_id, extracted if isinstance(extracted, dict) else {}, tokens
            )
            if trace_ctx:
                end_span(trace_ctx, "entity_linking", outputs={"links_count": n_links})
            if n_links:
                flow.step("entity_link", "ok", f"{n_links} cross-page entity link(s)")
        except Exception as e:  # pragma: no cover - linking must never fail the doc
            if trace_ctx:
                end_span(trace_ctx, "entity_linking", error=str(e))
            flow.step("entity_link", "warn", f"skipped ({e})")


async def _finalize(ctx: PipelineContext) -> None:
    """Terminal success path: set doc/job status, multi-branch route label, webhook, end trace.

    Faithful copy of ``pipeline._run`` l.1506-1537.
    """
    session = ctx.session
    cfg = ctx.cfg
    flow = ctx.flow
    document_id = ctx.document_id
    job = ctx.job
    new_status = ctx.new_status

    doc = await session.get(IdpDocument, document_id)  # refresh (save committed)
    doc.status = new_status
    ctx.doc = doc
    _route_headers = (
        await session.exec(select(IdpExtractedHeader).where(IdpExtractedHeader.job_id == job.id))
    ).all()
    route_label = _match_route(cfg, doc, headers=_route_headers)
    if route_label is not None:
        doc.route_label = route_label
        ctx.route_label = route_label
        flow.step("route_branch", "ok", f"multi-branch route: {route_label}")
    doc.processing_completed_at = _utcnow()
    ms = int((time.monotonic() - ctx.t0) * 1000)
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
        ctx.storage, ctx.agent_scope, f"flow_logs/{document_id}/flow.log", flow.render().encode("utf-8")
    )
    await _maybe_webhook(cfg, document_id, job.id, ctx.extracted, new_status, flow)
    if ctx.trace_ctx:
        await end_idp_trace(ctx.trace_ctx, outputs={"status": new_status}, processing_time_ms=ms)


# ─────────────────────────────── the runner ───────────────────────────────


async def run_graph_pipeline(ctx: PipelineContext, plan: ExecutionPlan, graph: dict) -> None:
    """Run the agent's canvas nodes in graph order (flag-ON path), reaching parity with
    ``pipeline._run`` by reusing the standalone step functions + the copied backbone helpers.

    Executes the 13 anchored steps in order; the planned graph nodes run at their stage between
    the anchored steps. ``graph`` is the raw canvas dict (needed by the branch pruners and to map
    a ``PlannedNode.id`` back to its raw node dict for handler dispatch).
    """
    id_to_node = {n.get("id"): n for n in (graph.get("nodes") or []) if isinstance(n, dict)}

    async def _run_stage(stage: str) -> None:
        for pn in plan.nodes:
            if pn.stage == stage:
                await pn.handler(ctx, id_to_node.get(pn.id) or {})

    # 1. detect digital/scanned
    await _detect(ctx)

    # 2. pre_text planned nodes. Scan Corrector's preprocessing is anchored inside _acquire_text
    # (legacy location, in the scanned/mixed OCR path), so here it is only a graph-order marker —
    # running its real handler would double-preprocess. Match legacy.
    for pn in plan.nodes:
        if pn.stage == "pre_text":
            ctx.flow.step("node", "ok", pn.display_name)

    # 3. text acquisition (anchored) — RAW full token set, NO page selection yet — then text-stage
    # planned markers (PaddleOCR / Page Selector).
    await _acquire_text(ctx)
    await _run_stage("text")

    # 4. multi-doc split — on the FULL (pre-page-selection) token set, matching legacy (_run l.1128,
    # before page selection). If the file holds several documents, materialise a child doc+job each
    # and stop here; the children process independently.
    ctx.child_ids = await _hook_split(ctx.session, ctx.storage, ctx.doc, ctx.tokens, ctx.page_status, ctx.cfg, ctx.flow)
    if ctx.child_ids:
        await _finalize_split(ctx)
        return

    # 4b. anchored VISUAL ELEMENT DETECTION (cfg-gated) — matches legacy _run l.1168: runs BEFORE
    # page-selection/merge AND before classify (so a classify-SKIPPED doc still gets detection, D-1).
    # Reads the ORIGINAL bytes (independent of page selection, so order vs merge is immaterial to its
    # output — placed here purely for exact legacy fidelity). cfg.detect_enabled is true when a Visual
    # Element Detection node exists OR idp_agent.extra sets detect_enabled (D-2).
    if getattr(ctx.cfg, "detect_enabled", False):
        await _hook_detect(ctx.session, ctx.document_id, ctx.original_bytes, ctx.file_type, ctx.cfg, ctx.flow)

    # 4c. page selection + merged-text build (anchored) — matching legacy (_run l.1179). This is where
    # a Page Selector limits the working token set (the classify step below consumes merged_text).
    await _select_and_merge(ctx)

    # 5. post_text planned markers (Document Classifier, Chunking Strategy) — graph-order flow log.
    await _run_stage("post_text")

    # 5b. anchored CLASSIFICATION (cfg-gated) — matches legacy _run l.1221. cfg.classify_enabled is
    # true when a Document Classifier node exists OR extra sets classify_enabled (D-2). A "skip"
    # verdict short-circuits the run (finalize-as-skipped).
    if getattr(ctx.cfg, "classify_enabled", False):
        ctx.skipped = await _hook_classify(
            ctx.session, ctx.document_id, ctx.base_agent, ctx.merged_text, ctx.cfg, ctx.flow
        )
    if ctx.skipped:
        await _finalize_skipped(ctx)
        return

    # 6. presence pruning (Multi-Branch Router on predicted_type, Condition on a header) — inputs
    # available pre-extract.
    plan = prune_presence_branches(plan, ctx, graph)

    # 7. extract (anchored) + extract-stage marker (AI Field Extractor)
    await _extract_fields(ctx)
    await _run_stage("extract")

    # 8. post_extract planned markers (Visual Element Detection, Math Reconcile) — graph-order log.
    await _run_stage("post_extract")

    # 8b. anchored MATH RECONCILE (cfg-gated) — matches legacy _run l.1428, after extraction.
    # cfg.math_reconcile_enabled is true when a Math Reconcile node exists OR extra sets it (D-2).
    # Forwards page_images (vision route) + ocr_text, exactly like the legacy call site.
    if getattr(ctx.cfg, "math_reconcile_enabled", False):
        ctx.extracted = await _hook_math_reconcile(
            ctx.extracted, ctx.llm, ctx.job, ctx.cfg, ctx.flow,
            page_images=getattr(ctx, "page_images", None),
            ocr_text=(ctx.merged_text if (ctx.merged_text and ctx.merged_text.strip()) else None),
        )
        # Drop the reconcile usage marker before save/webhook, matching legacy _run l.1436-1437 —
        # else `_usage` would persist as a spurious extracted field and leak into the webhook payload.
        if isinstance(ctx.extracted, dict):
            ctx.extracted.pop("_usage", None)

    # 9. save + cross-page entity linking (anchored) — sets ctx.overall_conf
    await _save_and_link(ctx)

    # 10. SAP two/three-way matching (anchored, non-fatal)
    match_result: str | None = None
    try:
        match_result = await _hook_sap_matching(
            session=ctx.session,
            document_id=ctx.document_id,
            idp_agent_id=ctx.idp_agent.id,
            extracted=ctx.extracted if isinstance(ctx.extracted, dict) else {},
            flow=ctx.flow,
        )
    except Exception as _e:  # matching must never abort the pipeline
        ctx.flow.step("sap_match", "warn", f"skipped ({_e})")

    # 11. confidence pruning — valid now that ctx.overall_conf exists (log/branch record only; no
    # optional downstream nodes remain to run at this point).
    plan = prune_confidence_branches(plan, ctx, graph)

    # 12. route — the rules engine decides auto_approved vs pending_review.
    if ctx.trace_ctx:
        start_span(ctx.trace_ctx, "rules_evaluation", inputs={"overall_confidence": ctx.overall_conf})
    ctx.new_status = await _route(
        ctx.session, ctx.document_id, ctx.job.id, ctx.idp_agent.id, ctx.overall_conf, ctx.cfg, ctx.flow,
        match_result=match_result,
    )
    if ctx.trace_ctx:
        end_span(ctx.trace_ctx, "rules_evaluation", outputs={"new_status": ctx.new_status})

    # 13. finalize
    await _finalize(ctx)
