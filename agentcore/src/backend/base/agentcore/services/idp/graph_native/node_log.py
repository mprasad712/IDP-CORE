"""Bounded, secret-safe per-node input/output sampling for the native IDP engine's flow log.

Each executed vertex on the generic ``graph_langgraph`` engine carries its resolved INPUT params on
``vertex.params`` (populated by ``_resolve_params``) and its OUTPUT on ``vertex.built_result``.
:func:`sample_io` summarizes both into a compact ``{"input": {...}, "output": {...}}`` dict so the
report / Processed Docs view / export can show what went INTO and OUT of every node — matching the
richness the fixed pipeline already logs.

Everything here is best-effort and defensive: huge blobs (``tokens`` / ``page_images``) are dropped,
secrets (tokens / keys / passwords) are redacted, ``bytes`` are shown as a size, strings are clipped,
dicts/lists are capped, and ANY error yields ``None`` so io capture can never break a document run.
"""

from __future__ import annotations

import re
from typing import Any

# ── bounds ──────────────────────────────────────────────────────────────────
_TEXT_CLIP = 2000       # max chars of a top-level text sample (e.g. Message.text)
_STR_CLIP = 400         # max chars of a single value inside a summarized dict
_MAX_DICT_KEYS = 60     # max keys kept from any dict
_MAX_LIST = 12          # max list items summarized
_MAX_DATA_KEYS = 40     # max Message.data key NAMES listed
_MAX_DEPTH = 4          # recursion depth cap for nested dicts/lists
_MAX_PARAMS = 12        # max input params summarized per node (aggregate-size guard)

# Keys whose values are huge blobs — never store them in the log.
_DROP_KEYS = frozenset({"tokens", "page_images"})
# Key names that hold raw file/image blobs (base64 or bytes) — store a size marker, never the value.
_BLOB_KEY_RE = re.compile(r"content_bytes|file_bytes|file_data|image_data|image_url|images|_?b64", re.IGNORECASE)
# A long, punctuation-free base64 / data-url string — store a size marker, not a clipped prefix.
_B64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]{200,}$")
# Key names that name a secret — redact the value.
_REDACT_RE = re.compile(
    r"access_token|refresh_token|client_secret|api_key|password|secret|token",
    re.IGNORECASE,
)


def _clip(value: Any, n: int) -> str:
    """Return at most ``n`` chars of ``value`` as a string (ellipsis marker kept within the budget)."""
    s = "" if value is None else str(value)
    return s if len(s) <= n else s[: n - 1] + "…"


def _str_val(s: str, n: int) -> str:
    """Clip a string to ``n`` chars, but replace a long base64 / data-url blob with a size marker
    (so a clipped base64 prefix never bloats the log)."""
    if len(s) > 200 and (s.startswith("data:") or _B64_RE.match(s)):
        return f"<base64 {len(s)} chars>"
    return _clip(s, n)


def _is_model(value: Any) -> bool:
    """Whether ``value`` is a LanguageModel-like object (has a callable ``.invoke``) — never serialized."""
    return callable(getattr(value, "invoke", None))


def _model_summary(value: Any) -> str:
    """A short placeholder for a model object: its registry id if present, else ``<model>``."""
    mid = getattr(value, "registry_model_id", None)
    return f"<model {mid}>" if mid else "<model>"


def _safe_dict(d: Any, depth: int = 0) -> Any:
    """Copy a dict for the log: drop blob keys, redact secret keys, bound values (bytes/str/nested)."""
    if not isinstance(d, dict):
        return _safe_val(d, depth)
    out: dict = {}
    for i, (k, v) in enumerate(d.items()):
        if i >= _MAX_DICT_KEYS:
            break
        ks = str(k)
        if ks in _DROP_KEYS:  # (a) huge blob key — drop
            continue
        if _REDACT_RE.search(ks):  # (b) secret — redact
            out[ks] = "***"
            continue
        if _BLOB_KEY_RE.search(ks):  # (b2) raw file/image blob — store a size marker, never the value
            out[ks] = f"<{len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else f"<blob {len(str(v))} chars>"
            continue
        out[ks] = _safe_val(v, depth + 1)
    return out


def _safe_val(v: Any, depth: int = 0) -> Any:
    """Bound a single value living inside a summarized dict/list (strings clip to ``_STR_CLIP``)."""
    if depth > _MAX_DEPTH:
        return "<…>"
    if _is_model(v):
        return _model_summary(v)
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, (bytes, bytearray)):  # (c) truncate raw bytes to a size marker
        return f"<{len(v)} bytes>"
    if isinstance(v, str):  # (d) clip long strings (base64 blobs → size marker)
        return _str_val(v, _STR_CLIP)
    if isinstance(v, dict):
        return _safe_dict(v, depth)
    if isinstance(v, (list, tuple)):
        return [_safe_val(x, depth + 1) for x in list(v)[:_MAX_LIST]]
    # Message / Data nested inside the payload → summarize with the top-level serializer.
    summ = _summ_schema(v, depth)
    if summ is not None:
        return summ
    return _clip(v, _STR_CLIP)


def _summ_schema(value: Any, depth: int = 0) -> Any:
    """Summarize a ``Message`` / ``Data`` if that's what ``value`` is; else ``None`` (not a schema type)."""
    try:
        from agentcore.schema.data import Data
        from agentcore.schema.message import Message
    except Exception:  # noqa: BLE001 — schema import must never break logging
        return None

    if isinstance(value, Message):  # Message subclasses Data — check it first
        text = value.text if isinstance(value.text, str) else ""
        idp = _safe_dict((getattr(value, "additional_kwargs", None) or {}).get("idp", {}), depth + 1)
        data = getattr(value, "data", None)
        return {
            "text_sample": _clip(text, _TEXT_CLIP),
            "idp": idp,
            "data_keys": list(data)[:_MAX_DATA_KEYS] if isinstance(data, dict) and data else None,
        }
    if isinstance(value, Data):
        return {"data": _safe_dict(getattr(value, "data", {}) or {}, depth + 1)}
    return None


def _summ(value: Any, depth: int = 0) -> Any:
    """Bounded, safe summary of an arbitrary value (top-level entry for params + built_result)."""
    if depth > _MAX_DEPTH:
        return "<…>"
    if _is_model(value):
        return _model_summary(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return _str_val(value, _TEXT_CLIP)
    # Message / Data get structured summaries.
    schema = _summ_schema(value, depth)
    if schema is not None:
        return schema
    if isinstance(value, dict):
        return _safe_dict(value, depth)
    if isinstance(value, (list, tuple)):
        return [_summ(x, depth + 1) for x in list(value)[:_MAX_LIST]]
    return _clip(value, _TEXT_CLIP)


def sample_io(vertex: Any) -> dict | None:
    """Return a bounded ``{"input": {...}, "output": {...}}`` sample for a built vertex, or ``None``.

    INPUT is a per-param summary of ``vertex.params`` (resolved inputs); OUTPUT summarizes
    ``vertex.built_result``. A side is omitted when empty; ``None`` is returned when there is nothing
    to sample or on ANY error — io capture must never break a run.
    """
    try:
        result: dict = {}

        # INPUT — resolved params (model objects collapse to a short placeholder). Cap the NUMBER of
        # params summarized so a node with many large upstream Messages can't bloat io.input.
        params = getattr(vertex, "params", None)
        if isinstance(params, dict) and params:
            # Drop the vestigial `model_name` free-text field (a leftover "gpt-4o" default on older
            # saved graphs): it is NOT the model that runs — the real model is the `llm` param. Logging
            # it just misleads ("why gpt-4o when I ran Groq?").
            pairs = [(k, v) for k, v in params.items() if str(k) != "model_name"]
            inp = {str(k): _summ(v) for k, v in pairs[:_MAX_PARAMS]}
            if len(pairs) > _MAX_PARAMS:
                inp["…"] = f"+{len(pairs) - _MAX_PARAMS} more param(s)"
            if inp:
                result["input"] = inp

        # OUTPUT — the built result (Message / Data / dict / other).
        built = getattr(vertex, "built_result", None)
        if built is not None:
            out = _summ(built)
            if out not in (None, "", {}, []):
                result["output"] = out

        return result or None
    except Exception:  # noqa: BLE001 — logging must NEVER break a document run
        return None
