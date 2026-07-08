"""Run a PUBLISHED IDP agent with a document attachment. Two equivalent entry points:

  * ``POST /api/run/{agent_id}``           JSON body, ``{"document": {"content_base64": "..."}}``
  * ``POST /api/run/{agent_id}/document``  multipart/form-data, a raw ``file`` — curl/Postman friendly

Both funnel into :func:`_process_document`, so they cannot drift. The multipart route exists because
``/api/run`` takes a JSON body and FastAPI cannot mix a JSON body with an ``UploadFile`` on one route.

``/api/run`` runs chat agents by building their graph. An IDP agent has no Chat Input — it runs the document
pipeline. This module is the IDP arm of that endpoint: it accepts the document, stamps the requested
``(env, version)`` onto the ``IdpDocument`` row, runs the pipeline, waits, and returns the extraction.

It does NOT resolve or execute the graph itself. ``pipeline._run`` reads ``doc.run_env`` / ``doc.run_version``
and resolves the published snapshot from ``agent_deployment_{uat,prod}``, so an API run and a Document Upload
run of the same version execute byte-for-byte the same graph.

Kept out of ``api/endpoints.py`` on purpose: it calls ``get_storage_service()`` directly (not via ``Depends``),
so tests monkeypatch it exactly the way ``tests/test_idp_pipeline.py`` already does.

Four hazards this file exists to get right — see the comments at each site:
  H1  the request's DB session must not sit idle-in-transaction while the pipeline runs (pool exhaustion)
  H2  ``await task`` on the pipeline's task would propagate cancellation INTO the pipeline
  H3  ``expire_on_commit=False`` means the pre-run ORM objects never see the pipeline's writes
  H7  a ``multi_doc_split`` agent finishes with zero rows and N children still running
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, Response, status
from loguru import logger
from pydantic import BaseModel
from sqlmodel import or_, select

from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
from agentcore.services.deps import get_queue_service, get_settings_service, get_storage_service, session_scope
from agentcore.services.idp.pipeline import PipelineError, enqueue_document
from agentcore.services.idp.snapshot import DEV, normalize_env
from agentcore.services.idp.storage_scope import storage_scope

from agentcore.api.idp.processed_docs import (
    DetectedElementRead,
    ExtractedHeaderRead,
    ExtractedLineItemRead,
    build_processed_doc_detail,
)

#: Bound how many API-triggered documents may occupy the event loop + connection pool at once. Each run
#: holds one asyncio task and the pipeline's own sessions for as long as the document takes.
_MAX_CONCURRENT = max(1, int(os.getenv("IDP_API_MAX_CONCURRENT_RUNS", "8")))
_RUN_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)

#: A job is finished when it is no longer waiting or working. `_run` never leaves it in-between.
_TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})

_DATA_URL_RE = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)


class IdpRunResponse(BaseModel):
    """What an IDP run returns.

    * ``200`` — the pipeline finished. A pipeline *failure* is still a 200, with ``status='failed'`` and
      ``error_message`` set: a non-2xx would make most clients discard the body, and the body is the only
      place the diagnostic lives.
    * ``202`` — it is still running after ``idp_api_run_timeout_seconds``. ``status='processing'`` and
      ``poll_url`` tells you where to fetch the result. Nothing was cancelled or lost.
    """

    document_id: UUID
    job_id: UUID | None
    environment: str
    version: str
    #: processing | auto_approved | pending_review | skipped | failed
    #: (`skipped` = the classifier filtered the document out; `processing` only ever accompanies a 202)
    status: str
    predicted_type: str | None = None
    overall_confidence: float | None = None
    headers: list[ExtractedHeaderRead] = []
    line_items: list[ExtractedLineItemRead] = []
    detected_elements: list[DetectedElementRead] = []
    error_message: str | None = None
    #: Only on a 202. GET it with the same x-api-key to collect the finished extraction.
    poll_url: str | None = None


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _max_attachment_bytes() -> tuple[int, int]:
    """``(max_mb, max_bytes)`` for one attachment.

    ``max_file_size_upload`` is applied by ``ContentSizeLimitMiddleware`` to the RAW body and defaults to
    1024 MB — far too permissive for a base64 payload (≈4/3 size, held in memory ~3x over). The dedicated
    ``idp_api_max_attachment_mb`` caps it.
    """
    settings = get_settings_service().settings
    max_mb = min(settings.max_file_size_upload, getattr(settings, "idp_api_max_attachment_mb", 25))
    return max_mb, max_mb * 1024 * 1024


def _too_large(max_mb: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"Attachment exceeds the maximum allowed size of {max_mb}MB.",
    )


def parse_document_id(raw) -> UUID | None:
    """``None`` / ``""`` / whitespace -> None (the server mints one). Anything else must be a valid UUID.

    Postman sends an *empty string* for a form row you leave blank, and typing the param as ``UUID | None``
    makes FastAPI reject that with a 422 before the handler ever runs — an obstacle for the exact caller
    who does not care about the id. Treat blank as absent; reject only genuine garbage.
    """
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return UUID(text)
    except (TypeError, ValueError) as exc:
        raise _bad_request(f"document_id must be a UUID (or omitted), got {raw!r}.") from exc


def _clean_filename(raw: str | None, *, field: str) -> str:
    """Reduce a client-supplied name to a bare basename.

    `os.path.basename` only splits on the SERVER's separator: on Linux it leaves
    ``C:\\docs\\invoice.pdf`` untouched, and a Windows client sends exactly that. Split on both, so
    ``original_filename`` never carries a path. (The stored blob is named ``idp_{uuid}{ext}``, so this is
    about display and hygiene, not traversal.)
    """
    name = str(raw or "").strip().replace("\r", "").replace("\n", "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        raise _bad_request(f"{field} is required.")
    return name


def _validate_extension(filename: str) -> str:
    from agentcore.api.idp.documents import ALLOWED_EXTENSIONS

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise _bad_request(
            f"File extension '{ext}' is not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    return ext


def _decode_attachment(attachment) -> tuple[str, bytes, str]:
    """JSON path: ``(filename, file_bytes, extension)``. Validates extension + size. Raises 400/413."""
    filename = _clean_filename(getattr(attachment, "filename", None), field="document.filename")
    ext = _validate_extension(filename)
    max_mb, max_bytes = _max_attachment_bytes()

    b64 = _DATA_URL_RE.sub("", str(attachment.content_base64 or "").strip())
    if not b64:
        raise _bad_request("document.content_base64 is empty.")

    # Reject on the ENCODED length first, so an oversized payload is never materialized as bytes.
    if (len(b64) * 3) // 4 > max_bytes:
        raise _too_large(max_mb)
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad_request(f"document.content_base64 is not valid base64: {exc}") from exc
    if not data:
        raise _bad_request("document.content_base64 decoded to zero bytes.")
    if len(data) > max_bytes:
        raise _too_large(max_mb)
    return filename, data, ext


async def _read_upload(upload) -> tuple[str, bytes, str]:
    """Multipart path: ``(filename, file_bytes, extension)``. Same validation, no base64 round-trip."""
    filename = _clean_filename(getattr(upload, "filename", None), field="file")
    ext = _validate_extension(filename)
    max_mb, max_bytes = _max_attachment_bytes()

    # Prefer the Content-Length-derived size so an oversized file is rejected BEFORE buffering it,
    # mirroring api/idp/documents.upload_documents.
    if getattr(upload, "size", None) is not None and upload.size > max_bytes:
        raise _too_large(max_mb)
    data = await upload.read()
    if not data:
        raise _bad_request("file is empty.")
    if len(data) > max_bytes:
        raise _too_large(max_mb)
    return filename, data, ext


async def _resolve_idp_agent(session, agent_id: UUID) -> IdpAgent:
    """The ``IdpAgent`` row for this base agent. NEVER auto-creates — that is a draft-only convenience."""
    idp_agent = (
        await session.exec(
            select(IdpAgent)
            .where(or_(IdpAgent.id == agent_id, IdpAgent.agent_id == agent_id))
            .where(IdpAgent.deleted_at.is_(None))
        )
    ).first()
    if idp_agent is None:
        raise _bad_request(
            f"Agent '{agent_id}' has no IDP configuration. Open it in the builder, add the IDP nodes, "
            f"save, and publish."
        )
    return idp_agent


async def _await_job(job_id: UUID, timeout: float | None) -> bool:
    """Wait for the pipeline job. Returns ``True`` if it finished, ``False`` if ``timeout`` elapsed first.

    NEVER cancels the pipeline, never raises. A timeout only stops *this request* from waiting — the job
    keeps running, finishes, and persists, and the caller fetches the result from
    ``GET /api/run/{agent_id}/documents/{document_id}``. ``timeout=None`` waits indefinitely.

    That is safe because ``RequestCancelledMiddleware`` is not registered (``main.py``), so a client
    disconnect does not cancel this handler either.
    """
    task = None
    try:
        _queue, _events, task, _ts = get_queue_service().get_queue_data(str(job_id))
    except Exception:  # noqa: BLE001 - queue missing / service closed -> fall back to polling
        task = None

    if task is not None:
        # NOT `await task`: cancelling THIS request task would propagate into the pipeline task and kill
        # the run, and `asyncio.wait_for` would cancel it on timeout. `asyncio.wait` does neither — it
        # returns (done, pending) and leaves the task alone. Same trick as `pipeline._reap_when_done`.
        done, _pending = await asyncio.wait([task], timeout=timeout)
        return bool(done)

    deadline = None if timeout is None else (asyncio.get_running_loop().time() + timeout)
    while True:
        async with session_scope() as poll_session:
            job = await poll_session.get(IdpProcessingJob, job_id)
            if job is None or job.status in _TERMINAL_JOB_STATES:
                return True
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.5)


def _common_guards(*, run_env: str, stream: bool, has_api_key: bool) -> None:
    """Cheapest-first. Nothing touches storage or the DB until these pass. Shared by both entry points."""
    if stream:
        raise _bad_request(
            "stream=true is not supported for IDP agents — the extraction is returned in the response body."
        )
    if run_env == DEV:
        raise _bad_request(
            "IDP agents can only be run from a published UAT or PROD version over the API. "
            "Use the Playground to run the draft."
        )
    if not has_api_key:
        # `_enforce_agent_api_key` fails OPEN: with no key row it auto-mints one and lets the call through.
        # Tolerable for a chat reply; not for a caller that writes documents and spends LLM tokens.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="An API key is required to run an IDP agent. Pass it via the x-api-key header.",
        )


async def run_idp_over_api(
    *,
    session,
    response: Response,
    agent,
    env_value: str,
    version: str,
    input_request,
    stream: bool,
    api_key_user,
    has_api_key: bool,
) -> IdpRunResponse:
    """JSON entry point: ``POST /api/run/{agent}`` with ``{"document": {"content_base64": ...}}``."""
    run_env = normalize_env(env_value)
    _common_guards(run_env=run_env, stream=stream, has_api_key=has_api_key)

    attachment = getattr(input_request, "document", None)
    if attachment is None:
        raise _bad_request(
            "This is an IDP agent: a document is required. Either send "
            '{"document": {"filename": "invoice.pdf", "content_base64": "<base64>"}} here, or POST the '
            "raw file as multipart/form-data to /api/run/{agent_id}/document."
        )
    filename, file_bytes, ext = _decode_attachment(attachment)

    return await _process_document(
        session=session, response=response, agent=agent, run_env=run_env, version=version,
        filename=filename, file_bytes=file_bytes, ext=ext,
        mime_type=getattr(attachment, "mime_type", None),
        document_id=parse_document_id(getattr(attachment, "document_id", None)),
        api_key_user=api_key_user,
    )


async def run_idp_over_api_multipart(
    *,
    session,
    response: Response,
    agent,
    env_value: str,
    version: str,
    upload,
    document_id,
    api_key_user,
    has_api_key: bool,
) -> IdpRunResponse:
    """Multipart entry point: ``POST /api/run/{agent}/document`` with a raw file.

    Identical semantics to the JSON path — it exists because ``/api/run`` takes a JSON body and FastAPI
    cannot mix a JSON body with an ``UploadFile`` on one route, and because base64-encoding a PDF by hand
    is a genuine obstacle to testing with curl/Postman.
    """
    run_env = normalize_env(env_value)
    _common_guards(run_env=run_env, stream=False, has_api_key=has_api_key)

    if upload is None:
        raise _bad_request("This is an IDP agent: a `file` form field containing the document is required.")
    filename, file_bytes, ext = await _read_upload(upload)

    return await _process_document(
        session=session, response=response, agent=agent, run_env=run_env, version=version,
        filename=filename, file_bytes=file_bytes, ext=ext,
        mime_type=getattr(upload, "content_type", None),
        document_id=parse_document_id(document_id),
        api_key_user=api_key_user,
    )


async def _process_document(
    *,
    session,
    response: Response,
    agent,
    run_env: str,
    version: str,
    filename: str,
    file_bytes: bytes,
    ext: str,
    mime_type: str | None,
    document_id,
    api_key_user,
) -> IdpRunResponse:
    """The single code path both entry points run: persist, enqueue, wait, serialize."""
    idp_agent = await _resolve_idp_agent(session, agent.id)
    if idp_agent.multi_doc_split:
        # `_run` would finish with status='split', ZERO extracted rows, and N child documents still
        # processing in their own tasks — there is no single extraction to return.
        raise _bad_request(
            "This agent has multi-document split enabled, which has no single extraction to return over "
            "the API. Use the Document Upload page, or disable multi-document split."
        )

    # An id supplied by the caller makes a retry idempotent and lets them poll for the result if the
    # connection dies mid-run. If we've seen it before, return what we have instead of re-processing.
    document_id = document_id or uuid4()
    existing = await session.get(IdpDocument, document_id)
    if existing is not None:
        logger.info(f"[idp-api] document {document_id} already exists — returning its current result")
        return _with_headers(response, await _build_response(document_id, run_env, version))

    storage_filename = f"idp_{document_id}{ext}"
    storage = get_storage_service()
    # Directory = base agent id + "(name_env_version)". `file_path` records it; readers split that.
    doc_scope = storage_scope(agent.id, getattr(agent, "name", None), run_env, version)
    await storage.save_file(agent_id=doc_scope, file_name=storage_filename, data=file_bytes)

    try:
        doc = IdpDocument(
            id=document_id,
            agent_id=idp_agent.id,
            original_filename=filename,
            file_path=f"{doc_scope}/{storage_filename}",
            file_type=ext.lstrip("."),
            mime_type=mime_type,
            file_size_bytes=len(file_bytes),
            checksum=hashlib.sha256(file_bytes).hexdigest(),
            source="api",
            source_metadata={"env": run_env, "version": version},
            status="queued",
            # NEVER `agent_api_key.created_by` — an auto-minted key stores the AGENT id there, not a user.
            # `api_key_user` is a resolved User|None, so this is safe by construction. NULL is fine: the
            # pipeline only uses it for a best-effort display name (connector ingest leaves it NULL too).
            uploaded_by=api_key_user.id if api_key_user else None,
            # The pipeline resolves the published snapshot from these two columns.
            run_env=run_env,
            run_version=version,
        )
        session.add(doc)
        await session.commit()
    except Exception:
        # Don't leave an orphaned blob behind if the row insert fails.
        try:
            await storage.delete_file(agent_id=doc_scope, file_name=storage_filename)
        except Exception:  # noqa: BLE001
            logger.warning(f"[idp-api] could not clean up orphaned file {storage_filename}")
        raise

    async with _RUN_SEMAPHORE:
        try:
            job_id = await enqueue_document(session, document_id)
        except PipelineError as exc:
            raise _bad_request(str(exc)) from exc

        # H1: `enqueue_document` ends with `session.refresh(job)`, which AUTOBEGINS a transaction it never
        # commits. Waiting minutes with that open holds a pooled connection "idle in transaction" and, under
        # concurrency, starves the pool that chat requests share. Close it, then never touch `session` again.
        await session.commit()

        timeout = getattr(get_settings_service().settings, "idp_api_run_timeout_seconds", 300) or None
        logger.info(
            f"[idp-api] {document_id}: running {run_env.upper()} {version} (job {job_id}), "
            f"waiting up to {timeout or '∞'}s"
        )
        finished = await _await_job(job_id, timeout)

    if not finished:
        # The pipeline was NOT cancelled — it keeps running and persists. Hand back the id so the caller
        # can collect the result instead of losing it to a client/proxy timeout.
        logger.info(f"[idp-api] {document_id}: still running after {timeout}s — returning 202 + poll_url")
        response.status_code = status.HTTP_202_ACCEPTED
        return _with_headers(response, IdpRunResponse(
            document_id=document_id, job_id=job_id, environment=run_env, version=version,
            status="processing", poll_url=f"/api/run/{agent.id}/documents/{document_id}",
        ))

    return _with_headers(response, await _build_response(document_id, run_env, version))


def _with_headers(response: Response, result: IdpRunResponse) -> IdpRunResponse:
    """Echo the ids as headers so a caller can still find the document if it can't parse the body."""
    response.headers["X-Idp-Document-Id"] = str(result.document_id)
    if result.job_id:
        response.headers["X-Idp-Job-Id"] = str(result.job_id)
    return result


async def _build_response(document_id: UUID, run_env: str, version: str) -> IdpRunResponse:
    """Read the finished document in a FRESH session and serialize it.

    H3: sessions are created with ``expire_on_commit=False``, so the ``doc``/``job`` objects the request
    session already holds were never refreshed with the pipeline's writes — reading ``doc.status`` off them
    would return ``"queued"`` forever.
    """
    async with session_scope() as read_session:
        doc = await read_session.get(IdpDocument, document_id)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        job = (
            await read_session.exec(
                select(IdpProcessingJob)
                .where(IdpProcessingJob.document_id == document_id)
                .order_by(IdpProcessingJob.created_at.desc(), IdpProcessingJob.id.desc())
                .limit(1)
            )
        ).first()
        detail = await build_processed_doc_detail(read_session, doc)

    return IdpRunResponse(
        document_id=document_id,
        job_id=job.id if job else None,
        environment=run_env,
        version=version,
        # `doc.status` is the pipeline's verdict: auto_approved | pending_review | skipped | failed.
        status=doc.status,
        predicted_type=detail.predicted_type,
        overall_confidence=detail.overall_confidence,
        headers=detail.headers,
        line_items=detail.line_items,
        detected_elements=detail.detected_elements,
        error_message=detail.error_message,
    )


async def get_idp_run_result(*, agent, document_id: UUID) -> IdpRunResponse:
    """Fetch a previous API run's result. Backs the API-key-authenticated recovery endpoint.

    Scoped to ``agent``: a key for agent A can never read agent B's documents.
    """
    async with session_scope() as read_session:
        doc = await read_session.get(IdpDocument, document_id)
        if doc is None or doc.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        idp_agent = await read_session.get(IdpAgent, doc.agent_id)
        if idp_agent is None or idp_agent.agent_id != agent.id:
            # Same 404 as "missing" — never confirm that someone else's document exists.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        # Read what we need BEFORE the session closes rather than relying on a detached instance.
        run_env, run_version = normalize_env(doc.run_env), doc.run_version or ""

    return await _build_response(document_id, run_env, run_version)
