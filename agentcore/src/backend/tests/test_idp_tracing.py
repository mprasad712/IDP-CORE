import pytest
import os
from uuid import uuid4
from unittest.mock import MagicMock, patch, ANY

from agentcore.services.idp.idp_tracing import (
    _get_or_create_client,
    create_idp_trace,
    start_span,
    end_span,
    end_idp_trace,
    IdpTraceContext,
)

@pytest.fixture
def anyio_backend():
    return "asyncio"

def test_client_caching():
    # Test client caching
    with patch("langfuse.Langfuse") as mock_langfuse:
        client1 = _get_or_create_client("http://localhost:3001", "pk_test", "sk_test")
        client2 = _get_or_create_client("http://localhost:3001", "pk_test", "sk_test")
        assert client1 is client2
        assert mock_langfuse.call_count == 1

@pytest.mark.anyio
async def test_tracing_deactivated():
    # Test that create_idp_trace returns an inactive context if DEACTIVATE_TRACING is set
    with patch.dict(os.environ, {"DEACTIVATE_TRACING": "true"}):
        ctx = await create_idp_trace(
            session=None,
            document_id=uuid4(),
            job_id=uuid4(),
            agent_id=uuid4(),
            agent_name="Test Agent",
            user_id=uuid4(),
        )
        assert ctx.ready is False
        assert ctx.client is None

@pytest.mark.anyio
async def test_trace_lifecycle_with_mock_client():
    mock_client = MagicMock()
    mock_span_ctx = MagicMock()
    mock_span = MagicMock()
    
    mock_span_ctx.__enter__.return_value = mock_span
    mock_client.start_as_current_observation.return_value = mock_span_ctx

    doc_id = uuid4()
    job_id = uuid4()
    agent_id = uuid4()
    user_id = uuid4()

    # We mock _get_or_create_client and resolve_write_langfuse_binding
    with patch("agentcore.services.idp.idp_tracing._get_or_create_client", return_value=mock_client), \
         patch("agentcore.services.observability.resolve_write_langfuse_binding", return_value=None), \
         patch.dict(os.environ, {"DEACTIVATE_TRACING": "false"}):
        
        ctx = await create_idp_trace(
            session=None,
            document_id=doc_id,
            job_id=job_id,
            agent_id=agent_id,
            agent_name="Test Agent",
            user_id=user_id,
            original_filename="test_doc.pdf",
            file_type="pdf",
        )

        assert ctx.ready is True
        assert ctx.client is mock_client
        assert ctx.document_id == doc_id
        assert ctx.job_id == job_id

        # Verify start_as_current_observation was called for the root span/trace
        mock_client.start_as_current_observation.assert_any_call(
            as_type="span",
            name="idp-pipeline-test_doc.pdf",
            metadata=ANY,
        )

        # ── Test start_span ──
        start_span(ctx, "ocr", inputs={"lang": "eng"})
        assert "ocr" in ctx.active_spans
        
        # ── Test end_span ──
        end_span(
            ctx,
            "ocr",
            outputs={"char_count": 120},
            usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            model="gpt-4o",
        )
        assert "ocr" not in ctx.active_spans
        assert ctx.total_input_tokens == 10
        assert ctx.total_output_tokens == 20
        assert ctx.total_tokens == 30
        assert ctx.primary_model == "gpt-4o"

        # ── Test end_idp_trace ──
        await end_idp_trace(ctx, outputs={"status": "success"})
        mock_span.update.assert_any_call(
            output=ANY,
            metadata={
                "pipeline_type": "idp",
                "total_input_tokens": 10,
                "total_output_tokens": 20,
                "total_tokens": 30,
                "processing_time_ms": None,
            },
            model="gpt-4o",
        )
        mock_client.flush.assert_called_once()
