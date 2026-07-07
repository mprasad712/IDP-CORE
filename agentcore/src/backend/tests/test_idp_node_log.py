"""Per-node input/output sampling for the native IDP engine's flow log (``graph_native/node_log.py``).

Proves ``sample_io(vertex)`` returns a bounded, secret-safe ``{"input", "output"}`` dict summarizing a
vertex's resolved inputs (``vertex.params``) and its output (``vertex.built_result``), and that it can
NEVER raise (io capture must never break a run).
"""

from __future__ import annotations

import types

from agentcore.schema.data import Data
from agentcore.schema.message import Message
from agentcore.services.idp.graph_native.node_log import sample_io


def test_sample_io_bounds_and_redacts():
    # An IDP node's input: a Message carrying the working set in additional_kwargs["idp"]. That block
    # holds huge blobs (tokens/page_images), a secret (access_token) and a normal field (vendor).
    msg = Message(
        text="A" * 5000,
        additional_kwargs={
            "idp": {
                "document_id": "d1",
                "tokens": [1, 2, 3, 4],
                "page_images": ["<big>", "<blobs>"],
                "access_token": "SECRET",
                "vendor": "ACME",
                "extracted": {"headers": {"total": {"value": "100"}}},
            }
        },
    )
    # The node's output: a Data whose data dict carries extracted headers + a secret to redact.
    out = Data(data={"headers": {"invoice_no": {"value": "INV-1"}}, "refresh_token": "X"})

    v = types.SimpleNamespace(params={"document": msg}, built_result=out)

    io = sample_io(v)

    # Exactly the {input, output} shape the frontend reads (s.io.input / s.io.output) — no extra keys.
    assert isinstance(io, dict)
    assert set(io) == {"input", "output"}

    # INPUT: the Message summary is bounded + safe.
    doc = io["input"]["document"]
    assert len(doc["text_sample"]) <= 2000  # 5000-char text clipped
    idp = doc["idp"]
    assert "tokens" not in idp  # huge blob dropped
    assert "page_images" not in idp  # huge blob dropped
    assert idp["access_token"] == "***"  # secret redacted
    assert idp["vendor"] == "ACME"  # normal field kept
    assert idp["document_id"] == "d1"

    # OUTPUT: the Data summary carries the extracted headers, with secrets redacted.
    assert io["output"]["data"]["headers"]  # present
    assert io["output"]["data"]["refresh_token"] == "***"  # secret redacted


def test_sample_io_never_raises():
    # A junk object with nothing to sample → None (no exception).
    assert sample_io(object()) is None

    # An object whose params access raises → the error is swallowed → None.
    class Boom:
        @property
        def params(self):
            raise RuntimeError("boom")

    assert sample_io(Boom()) is None
