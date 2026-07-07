"""Task 2: FlowLog.render() must fold the bounded io payloads into the exported text artifact
so a downloaded flow.log shows full per-step input/output, not just the one-line summary."""
from uuid import uuid4

from agentcore.services.idp.pipeline import FlowLog


def test_render_includes_io_payloads():
    fl = FlowLog(uuid4(), "invoice.pdf")
    fl.step(
        "extract", "ok", "LLM returned 3 headers",
        io={"input": {"chars": 123, "text_sample": "TAX INVOICE hello"},
            "output": {"headers": {"invoice_number": "INV-1"}, "overall_confidence": 0.94}},
        ms=42,
    )
    txt = fl.render()
    # one-line summary still present
    assert "LLM returned 3 headers" in txt
    # io blocks folded into the export
    assert "input:" in txt and "output:" in txt
    assert "text_sample" in txt and "TAX INVOICE hello" in txt
    assert "invoice_number" in txt and "INV-1" in txt
    assert "42 ms" in txt


def test_render_without_io_unchanged():
    fl = FlowLog(uuid4(), "plain.pdf")
    fl.step("load", "ok", "loaded")
    txt = fl.render()
    assert "loaded" in txt
    assert "input:" not in txt and "output:" not in txt  # no io -> no io block
