"""Which storage directory an IDP document's artifacts live in.

The directory used to be ``idp_agents.id`` — an id that appears nowhere in the API, the URL bar, or the
UI. ``POST /api/run/582bc691-…/document`` wrote into ``8fcd9733-…/``, and nothing on screen connected the
two. Now it is the **base agent id** (the one in the URL), annotated with a human-readable suffix:

    582bc691-298b-423e-8daf-55a7f56a96c7(testing-universal-agent_uat_v5)/
        idp_<document_id>.pdf
        corrected/corrected_idp_<document_id>.pdf
        flow_logs/<document_id>/flow.log

The suffix carries the run target of the documents inside, so an agent gets one directory per
``(env, version)`` it has actually processed documents for. That is coherent — every document in
``…_uat_v5`` really was run against UAT v5 — and it is why the version belongs here at all.

READS NEVER RECONSTRUCT THIS. ``IdpDocument.file_path`` stores ``<scope>/<file_name>`` at upload time and
every reader splits it (:func:`scope_of`). That is what makes this change safe: the ~86 documents already on
disk under an ``idp_agents.id`` directory keep resolving from their own stored path, forever. It is also why
an agent rename cannot break anything — a renamed agent simply starts writing into a new directory.
"""

from __future__ import annotations

import re
from typing import Any

#: Windows rejects  \ / : * ? " < > |  and trailing dots/spaces. Parentheses and underscores are fine.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_NAME_LIMIT = 40


def slugify_agent_name(name: str | None) -> str:
    """``'testing universal agent'`` -> ``'testing-universal-agent'``. Never empty, never path-hostile."""
    slug = _UNSAFE.sub("-", str(name or "").strip()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)[:_NAME_LIMIT].strip("-._")
    return slug or "agent"


def storage_scope(agent_id: Any, agent_name: str | None, run_env: Any = None, run_version: str | None = None) -> str:
    """The directory name for documents this agent runs against ``(run_env, run_version)``.

    ``run_env`` is normalized ('1' -> 'uat'); an absent env means the draft canvas. A published run without
    an explicit version pins to whatever was active, so it is labelled ``latest`` rather than lying. The
    draft has exactly ONE version, so any ``run_version`` passed alongside ``dev`` is ignored rather than
    written into the directory name — otherwise the same draft canvas would scatter across `_dev_v1`,
    `_dev_v2`, … depending on what the caller happened to pass.
    """
    from agentcore.services.idp.snapshot import DEV, normalize_env

    env = normalize_env(run_env)
    if env == DEV:
        version = "draft"
    else:
        version = (run_version or "").strip() or "latest"
    return f"{agent_id}({slugify_agent_name(agent_name)}_{env}_{slugify_agent_name(version)})"


def scope_of(file_path: str | None) -> str:
    """The directory a document ALREADY lives in, from its stored ``file_path``.

    Every read and every sibling artifact (corrected scans, flow logs, OCR dumps) must go through this, so
    a document written under the old ``idp_agents.id`` scheme keeps working unchanged.
    """
    path = str(file_path or "")
    return path.split("/", 1)[0] if "/" in path else ""
