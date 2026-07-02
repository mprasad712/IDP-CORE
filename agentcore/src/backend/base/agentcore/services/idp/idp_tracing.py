"""IDP Pipeline – Langfuse tracing integration.

Provides a lightweight, per-document tracing context that creates one
Langfuse **trace** per ``IdpProcessingJob`` and child **span/generation**
observations for each pipeline step (OCR, extraction, rules, etc.).

Design decisions:
  * We do NOT reuse the full ``TracingService`` / ``LangGraphAdapter`` stack
    because IDP documents are not graph runs. Instead, we use the Langfuse
    SDK directly (same ``_get_or_create_write_client`` factory as
    ``LangFuseTracer``) but with a simpler lifecycle.
  * Token usage is attached to ``generation``-type observations so it
    appears in the Langfuse UI cost/token columns.
  * All tracing is best-effort: failures are logged but NEVER crash the
    pipeline (the outer ``process_document`` guarantees this contract).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from loguru import logger


# ── Langfuse client cache (reused across documents) ────────────────────
_BLOCKED_SCOPES = [
    "fastapi", "starlette", "asgi",
    "opentelemetry.instrumentation.fastapi",
    "opentelemetry.instrumentation.starlette",
    "opentelemetry.instrumentation.asgi",
    "httpx", "aiohttp", "requests", "urllib3",
    "opentelemetry.instrumentation.httpx",
    "opentelemetry.instrumentation.aiohttp",
    "opentelemetry.instrumentation.requests",
    "opentelemetry.instrumentation.urllib3",
]

_CLIENT_CACHE: dict[str, Any] = {}


def _get_or_create_client(
    host: str | None,
    public_key: str | None,
    secret_key: str | None,
) -> Any:
    """Return a cached Langfuse client for IDP trace writing."""
    cache_key = f"idp:{host or ''}:{public_key or ''}"
    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("langfuse not installed — IDP tracing disabled")
        return None

    kwargs: dict[str, Any] = {}
    if host:
        kwargs["host"] = host
    if public_key:
        kwargs["public_key"] = public_key
    if secret_key:
        kwargs["secret_key"] = secret_key

    try:
        client = Langfuse(
            blocked_instrumentation_scopes=_BLOCKED_SCOPES,
            **kwargs,
        )
    except TypeError:
        try:
            client = Langfuse(**kwargs)
        except TypeError:
            from langfuse import get_client
            if kwargs:
                raise
            client = get_client()

    # Quick auth check
    if hasattr(client, "auth_check"):
        try:
            if not client.auth_check():
                logger.warning("Langfuse auth_check failed for IDP tracing — check credentials")
            else:
                logger.debug("Langfuse IDP tracing auth_check passed")
        except Exception as e:
            logger.warning(f"Langfuse auth_check error (continuing): {e}")

    _CLIENT_CACHE[cache_key] = client
    logger.info(f"Created and cached Langfuse IDP client for {cache_key}")
    return client


# ── Per-document trace context ─────────────────────────────────────────

@dataclass
class IdpSpan:
    """A tracked child span/generation within the IDP trace."""
    name: str
    context: Any = None
    span: Any = None
    start_time: float = 0.0


@dataclass
class IdpTraceContext:
    """Per-document Langfuse trace context."""

    document_id: UUID
    job_id: UUID
    agent_id: UUID | None = None
    agent_name: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    session_id: str | None = None
    environment: str | None = None

    # Langfuse client state
    client: Any = None
    ready: bool = False
    root_context: Any = None
    root_span: Any = None
    propagate_context: Any = None
    otel_reset_token: Any = None
    langfuse_trace_id: str | None = None

    # Active spans
    active_spans: dict[str, IdpSpan] = field(default_factory=dict)

    # Accumulated token usage for the entire document
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    primary_model: str | None = None


async def create_idp_trace(
    session,
    document_id: UUID,
    job_id: UUID,
    agent_id: UUID | None,
    agent_name: str | None,
    user_id: UUID | None,
    user_name: str | None = None,
    original_filename: str | None = None,
    file_type: str | None = None,
    environment: str | None = None,
    org_id: UUID | None = None,
    dept_id: UUID | None = None,
) -> IdpTraceContext:
    """Create a Langfuse trace for an IDP document processing job.

    Resolves the correct ``LangfuseBinding`` for the document's dept/org
    using the existing RBAC provisioning infrastructure. Returns an
    ``IdpTraceContext`` (always — even if tracing fails, the context is
    returned with ``ready=False`` so the pipeline can run unimpeded).
    """
    ctx = IdpTraceContext(
        document_id=document_id,
        job_id=job_id,
        agent_id=agent_id,
        agent_name=agent_name,
        user_id=str(user_id) if user_id else None,
        user_name=user_name,
        session_id=str(agent_id) if agent_id else None,  # group by agent
        environment=environment or "production",
    )

    # If tracing is globally deactivated, return early
    if str(os.getenv("DEACTIVATE_TRACING", "false")).strip().lower() in {"1", "true", "yes"}:
        logger.debug("IDP tracing deactivated via DEACTIVATE_TRACING")
        return ctx

    # ── Resolve Langfuse credentials via existing RBAC infrastructure ──
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    try:
        from agentcore.services.observability import (
            get_langfuse_provisioning_service,
            resolve_write_langfuse_binding,
        )

        binding = await resolve_write_langfuse_binding(
            session,
            user_id=user_id,
            agent_id=agent_id,
            selected_dept_id=dept_id,
        )

        if binding:
            service = get_langfuse_provisioning_service()
            langfuse_host = binding.langfuse_host
            langfuse_public_key = service.decrypt_secret(binding.public_key_encrypted)
            langfuse_secret_key = service.decrypt_secret(binding.secret_key_encrypted)
            logger.info(
                f"Resolved Langfuse binding for IDP trace: "
                f"user_id={user_id}, agent_id={agent_id}, binding_id={binding.id}"
            )
        else:
            # Fallback: use env vars directly (single-tenant local dev)
            langfuse_host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")
            logger.info(
                "No Langfuse binding resolved for IDP trace — "
                "falling back to LANGFUSE_HOST env var"
            )
    except Exception:
        logger.exception("Failed resolving Langfuse binding for IDP trace — falling back to env vars")
        langfuse_host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL")

    if not langfuse_host:
        logger.info("No Langfuse host configured — IDP tracing disabled for this document")
        return ctx

    # ── Create the Langfuse client and root trace ──
    try:
        client = _get_or_create_client(langfuse_host, langfuse_public_key, langfuse_secret_key)
        if client is None:
            return ctx
        ctx.client = client

        from langfuse import propagate_attributes

        trace_metadata = {
            "pipeline_type": "idp",
            "document_id": str(document_id),
            "job_id": str(job_id),
            "agent_id": str(agent_id) if agent_id else None,
            "agent_name": agent_name,
            "user_id": str(user_id) if user_id else None,
            "user_name": user_name,
            "original_filename": original_filename,
            "file_type": file_type,
            "environment": ctx.environment,
            "trace_created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if org_id:
            trace_metadata["org_id"] = str(org_id)
        if dept_id:
            trace_metadata["dept_id"] = str(dept_id)

        # Ensure a clean OTEL context so we start a NEW top-level trace
        try:
            from opentelemetry import context as otel_context
            ctx.otel_reset_token = otel_context.attach(otel_context.Context())
        except ImportError:
            pass

        # Create root span as the trace container
        trace_name = f"idp-pipeline-{original_filename or str(document_id)[:8]}"
        ctx.root_context = client.start_as_current_observation(
            as_type="span",
            name=trace_name,
            metadata=trace_metadata,
        )
        ctx.root_span = ctx.root_context.__enter__()

        # Propagate user_id / session_id
        langfuse_user_id = user_name or (str(user_id) if user_id else None)
        ctx.propagate_context = propagate_attributes(
            user_id=langfuse_user_id,
            session_id=ctx.session_id,
        )
        ctx.propagate_context.__enter__()

        ctx.ready = True

        # Extract the OTEL trace ID
        try:
            from opentelemetry import trace as otel_trace
            current_span = otel_trace.get_current_span()
            if current_span and current_span.get_span_context().is_valid:
                ctx.langfuse_trace_id = format(
                    current_span.get_span_context().trace_id, '032x'
                )
        except Exception:
            pass

        logger.info(
            f"IDP Langfuse trace started: doc={document_id}, "
            f"agent={agent_name}, otel_trace_id={ctx.langfuse_trace_id}"
        )

    except ImportError:
        logger.warning("langfuse not installed — IDP tracing disabled")
    except Exception:
        logger.exception("Error creating IDP Langfuse trace — tracing disabled for this document")

    return ctx


def start_span(
    ctx: IdpTraceContext,
    step_name: str,
    inputs: dict[str, Any] | None = None,
    observation_type: str = "span",
    registry_model_id: str | None = None,
) -> None:
    """Start a child span for a pipeline step.

    ``observation_type`` should be ``"generation"`` for LLM extraction calls
    (so Langfuse shows token usage) and ``"span"`` for everything else.
    """
    if not ctx.ready or not ctx.client:
        return

    try:
        from agentcore.serialization.serialization import serialize
        span_metadata = {
            "pipeline_type": "idp",
            "step": step_name,
            "document_id": str(ctx.document_id),
        }
        if registry_model_id:
            span_metadata["registry_model_id"] = registry_model_id

        span_context = ctx.client.start_as_current_observation(
            as_type=observation_type,
            name=step_name,
            input=serialize(inputs) if inputs else None,
            metadata=span_metadata,
        )
        span = span_context.__enter__()

        ctx.active_spans[step_name] = IdpSpan(
            name=step_name,
            context=span_context,
            span=span,
            start_time=time.monotonic(),
        )
    except Exception as e:
        logger.debug(f"Error starting IDP span '{step_name}': {e}")


def end_span(
    ctx: IdpTraceContext,
    step_name: str,
    outputs: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    model: str | None = None,
    error: str | None = None,
    registry_model_id: str | None = None,
) -> None:
    """End a child span for a pipeline step.

    ``usage`` should contain ``input_tokens``, ``output_tokens``,
    ``total_tokens`` from the LLM response. ``model`` is the model name.
    """
    if not ctx.ready:
        return

    idp_span = ctx.active_spans.pop(step_name, None)
    if not idp_span or not idp_span.span:
        return

    try:
        from agentcore.serialization.serialization import serialize

        update_payload: dict[str, Any] = {}
        if outputs:
            out = dict(outputs)
            if error:
                out["error"] = error
            update_payload["output"] = serialize(out)
        elif error:
            update_payload["output"] = serialize({"error": error})

        # Attach token usage for generation observations
        if usage:
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or 0)
            if total_tokens == 0 and (input_tokens or output_tokens):
                total_tokens = input_tokens + output_tokens

            usage_payload = {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
            }
            update_payload["usage_details"] = usage_payload
            update_payload["usage"] = usage_payload

            # Accumulate to trace-level totals
            ctx.total_input_tokens += input_tokens
            ctx.total_output_tokens += output_tokens
            ctx.total_tokens += total_tokens

        if model:
            update_payload["model"] = str(model)
            if not ctx.primary_model:
                ctx.primary_model = model

        # Add latency
        elapsed_ms = int((time.monotonic() - idp_span.start_time) * 1000)
        update_payload.setdefault("metadata", {})
        if isinstance(update_payload.get("metadata"), dict):
            update_payload["metadata"]["latency_ms"] = elapsed_ms
            if registry_model_id:
                update_payload["metadata"]["registry_model_id"] = registry_model_id

        if error:
            update_payload["level"] = "ERROR"
            update_payload["status_message"] = error

        if hasattr(idp_span.span, "update"):
            idp_span.span.update(**update_payload)

        # Exit the span context
        if idp_span.context:
            idp_span.context.__exit__(None, None, None)

    except Exception as e:
        logger.debug(f"Error ending IDP span '{step_name}': {e}")


async def end_idp_trace(
    ctx: IdpTraceContext,
    outputs: dict[str, Any] | None = None,
    error: Exception | None = None,
    processing_time_ms: int | None = None,
) -> None:
    """End the root IDP trace and flush to Langfuse.

    Always safe to call even if ``ctx.ready`` is False (no-op).
    """
    if not ctx.ready:
        return

    try:
        # Close any spans that were not explicitly ended
        for step_name in list(ctx.active_spans.keys()):
            end_span(ctx, step_name, error="pipeline ended before span completed")

        # Update root span with final outputs
        if ctx.root_span and hasattr(ctx.root_span, "update"):
            from agentcore.serialization.serialization import serialize

            root_update: dict[str, Any] = {
                "output": serialize(outputs or {}),
                "metadata": {
                    "pipeline_type": "idp",
                    "total_input_tokens": ctx.total_input_tokens,
                    "total_output_tokens": ctx.total_output_tokens,
                    "total_tokens": ctx.total_tokens,
                    "processing_time_ms": processing_time_ms,
                },
            }
            if error is not None:
                root_update["level"] = "ERROR"
                root_update["status_message"] = str(error)
            if ctx.primary_model:
                root_update["model"] = ctx.primary_model
            ctx.root_span.update(**root_update)

        # Exit propagate_attributes context
        if ctx.propagate_context:
            ctx.propagate_context.__exit__(None, None, None)

        # Exit root span context
        if ctx.root_context:
            ctx.root_context.__exit__(None, None, None)

    except Exception as e:
        logger.warning(f"Error ending IDP trace: {e}")

    # Detach the clean OTEL context
    if ctx.otel_reset_token is not None:
        try:
            from opentelemetry import context as otel_context
            otel_context.detach(ctx.otel_reset_token)
            ctx.otel_reset_token = None
        except Exception:
            pass

    # Flush
    try:
        if ctx.client and hasattr(ctx.client, "flush"):
            ctx.client.flush()
    except Exception:
        pass
    try:
        from opentelemetry import trace as otel_trace
        provider = otel_trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
    except Exception:
        pass

    logger.info(
        f"IDP Langfuse trace ended: doc={ctx.document_id}, "
        f"tokens={{in={ctx.total_input_tokens}, out={ctx.total_output_tokens}, "
        f"total={ctx.total_tokens}}}, model={ctx.primary_model}"
    )
