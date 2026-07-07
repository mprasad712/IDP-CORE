"""Run a document through the native ``graph_langgraph`` engine as a background job, streaming
per-vertex events under the ``IdpProcessingJob.id`` so the frontend can consume them via the
existing ``GET /build/{job_id}/events`` SSE endpoint — native canvas highlighting, no polling.

Dispatched from ``pipeline._run`` when the engine resolves to ``native``. The sink component
(``Processed Docs Output``) persists + finalizes the document; this just wires the event stream and
the job lifecycle around the run.
"""

from __future__ import annotations

import time

from loguru import logger

from agentcore.services.idp.graph_native.runner import run_native_graph


async def run_native_for_document(session, doc, job, base_agent, flow, trace_ctx=None) -> None:
    """Execute ``doc`` on the generic engine; stream vertex events under ``job.id``; finalize the job."""
    from agentcore.services.deps import get_queue_service

    job_key = str(job.id)
    event_manager = None
    try:
        # enqueue_document already created the queue + registered this run's task under str(job.id)
        # (that's what GET /build/{job_id}/events serves). REUSE that event manager so the frontend's
        # SSE consumer receives our per-vertex events; the queue buffers, so events emitted before the
        # consumer connects aren't lost. (Creating a second queue here would orphan the served one.)
        _, event_manager, _, _ = get_queue_service().get_queue_data(job_key)
    except Exception as e:  # streaming is best-effort — the run still persists without it
        logger.warning(f"[idp-native] no event queue for {job_key} (SSE highlighting disabled): {e}")

    logger.info(
        f"[idp-native] {job_key}: per-vertex event streaming "
        f"{'ON — canvas will highlight' if event_manager is not None else 'OFF — no event queue'}"
    )
    user_id = str(doc.uploaded_by) if getattr(doc, "uploaded_by", None) else None
    agent_name = getattr(base_agent, "name", None) or "IDP"
    t0 = time.monotonic()
    flow.step("engine", "ok", "IDP execution engine: native (generic graph_langgraph)")

    node_log: list = []
    run_result: dict = {}
    try:
        await run_native_graph(
            document_id=str(doc.id),
            agent_data=(getattr(base_agent, "data", None) or {}),
            agent_id=str(base_agent.id),
            agent_name=agent_name,
            user_id=user_id,
            session_id=str(doc.id),
            event_manager=event_manager,
            node_log_out=node_log,
            result_out=run_result,
        )
    finally:
        # Per-node log → flow steps (persisted to job.log / shown on the document's Log page).
        for entry in node_log:
            nm = entry.get("name") or "node"
            st = entry.get("status") or "ran"
            flow.step(f"node:{nm}", "ok", st, node_id=entry.get("id"))
        if event_manager is not None:
            try:
                event_manager.on_end(data={})  # closes the SSE stream
            except Exception:
                pass

    # The sink component (Processed Docs Output) normally finalizes doc.status. Safety net: if the
    # graph had no reachable sink (or it couldn't persist), the doc is still non-terminal and the
    # frontend would poll forever. Force a terminal status so the UI resolves, and log the gap.
    from agentcore.services.database.models.idp.documents import IdpDocument
    from agentcore.services.deps import session_scope

    _NON_TERMINAL = {None, "queued", "running", "processing"}
    try:
        async with session_scope() as _s:
            fresh = await _s.get(IdpDocument, doc.id)
            if fresh is not None and fresh.status in _NON_TERMINAL:
                # No sink finalized the doc — most often a router branch (High/Low, passed/failed, …)
                # was left unwired to a Processed Docs Output. Before forcing a status, SAVE the
                # extraction we harvested from the run so the fields are NOT lost to a wiring gap.
                saved_n = 0
                extracted = run_result.get("extracted")
                if isinstance(extracted, dict) and (extracted.get("headers") or extracted.get("line_items")):
                    try:
                        from agentcore.services.idp.extraction import save_extraction_results

                        rpayload = run_result.get("payload") or {}
                        await save_extraction_results(
                            session=_s,
                            document_id=doc.id,
                            job_id=job.id,
                            extraction_result=extracted,
                            ocr_tokens=rpayload.get("tokens") or [],
                            vision_mode=bool(rpayload.get("route") == "vision"),
                        )
                        saved_n = len(extracted.get("headers", {}) or {})
                    except Exception as e:  # saving is best-effort — still force a terminal status below
                        logger.warning(f"[idp-native] {doc.id}: safety-net extraction save failed: {e}")
                logger.warning(
                    f"[idp-native] {doc.id}: no sink finalized the doc (status={fresh.status}); "
                    f"rescued {saved_n} field(s) from the channel + forcing 'pending_review'. Wire a "
                    f"Processed Docs Output on the router branch the document actually takes."
                )
                fresh.status = "pending_review"
                _s.add(fresh)
                await _s.commit()
    except Exception as e:  # never let the safety net crash the run
        logger.warning(f"[idp-native] {doc.id}: could not verify/force terminal doc status: {e}")

    # Finalize the job row + persist the flow log.
    ms = int((time.monotonic() - t0) * 1000)
    job.status = "completed"
    from datetime import datetime, timezone

    job.completed_at = datetime.now(timezone.utc)
    job.processing_time_ms = ms
    job.steps_completed = flow.steps_ok
    job.log = flow.events
    session.add(job)
    await session.commit()
    logger.info(f"[idp-native] {doc.id}: completed on the generic engine in {ms} ms")
