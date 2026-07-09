"""The Field Configuration definitions a run extracts against — frozen at publish, or read live.

``idp_field_configurations`` (and its header / line-item children) are **mutated in place** — there is
no version column. The published graph snapshot only stores the config's *name*, which is resolved to an
id and then to its fields at run time. So editing "Invoice" silently changed what every published UAT /
PROD agent extracted, and deleting it silently fell back to a *global* catalogue config of the same name.

Publish therefore freezes the resolved definitions into ``_idp_agent_config.field_configs`` (see
``api/publish._freeze_idp_agent_config``). A published run reads the frozen copy; the draft (Playground,
connectors, pre-fix snapshots) still reads the live tables, so editing a config keeps giving instant
feedback on the canvas.

Why a ContextVar and not a parameter: the definitions are loaded in three places (``extract_named_config``,
``extract_multimodal``, ``pipeline._named_config_vision_messages``) and the name→id lookup in two more —
one of which is the ``AI Field Extractor`` canvas component, which opens its own session and never sees
``cfg``. The frozen set is scoped to one document's run, which is exactly one asyncio Task, so a ContextVar
is the natural carrier: everything awaited from ``_run`` (both engines, the isolated classify session) sees
it, and separate documents cannot see each other's.

``_run`` **always** calls :func:`install_for_graph` before any definition is loaded — passing a draft graph
installs an empty index — so a stale value can never survive into the next run.
"""

from __future__ import annotations

import contextlib
import contextvars
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlmodel import select

#: Key inside ``_idp_agent_config`` holding ``{config_name: {"id", "headers", "line_items"}}``.
FIELD_CONFIGS_KEY = "field_configs"

# Every column the prompt builders and extractors read. Kept explicit so a schema change surfaces here
# rather than as a silently-missing field in a published run's prompt.
_HEADER_COLS = ("field_name", "field_type", "is_required", "display_order", "description", "prompt")
_LINE_COLS = ("column_name", "column_type", "is_required", "display_order", "prompt")

_frozen: contextvars.ContextVar[dict | None] = contextvars.ContextVar("idp_frozen_field_defs", default=None)


class FieldConfigMissing(ValueError):
    """No definitions for this config — neither frozen in the snapshot nor live in the DB."""


# ───────────────────────────── run scope ─────────────────────────────
def _index(configs: Any) -> dict:
    """``{name: spec}`` -> ``{"by_name": {...}, "by_id": {...}}`` (names matched case-insensitively)."""
    by_name: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    if isinstance(configs, dict):
        for name, spec in configs.items():
            if not isinstance(spec, dict):
                continue
            by_name[str(name).strip().lower()] = spec
            if spec.get("id"):
                by_id[str(spec["id"])] = spec
    return {"by_name": by_name, "by_id": by_id}


def install_for_graph(graph: dict | None) -> dict:
    """Install the frozen definitions carried by ``graph`` as this run's set. Returns the index.

    A draft graph (or a snapshot published before this existed) yields an EMPTY index, which makes every
    lookup fall through to the live tables — the pre-existing behavior.
    """
    from agentcore.services.idp.snapshot import pinned_idp_config

    index = _index(pinned_idp_config(graph).get(FIELD_CONFIGS_KEY))
    _frozen.set(index)
    return index


@contextlib.contextmanager
def use_frozen_field_defs(configs: Any):
    """Scope a frozen set around a block (tests, and any caller outside ``_run``)."""
    token = _frozen.set(_index(configs))
    try:
        yield
    finally:
        _frozen.reset(token)


def _current() -> dict:
    return _frozen.get() or {"by_name": {}, "by_id": {}}


# ───────────────────────────── lookups ─────────────────────────────
def has_frozen_defs() -> bool:
    """True when this run carries frozen definitions (i.e. a published run on a post-fix snapshot)."""
    return bool(_current()["by_name"])


def frozen_spec_for_name(name: str | None) -> dict | None:
    if not name:
        return None
    return _current()["by_name"].get(str(name).strip().lower())


def frozen_id_for_name(name: str | None) -> UUID | None:
    """The config id this run froze for ``name``, or None to fall through to the live lookup."""
    raw = (frozen_spec_for_name(name) or {}).get("id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def frozen_doc_type(name: str | None) -> str | None:
    """The ``doc_type`` frozen for ``name``.

    Multi-type routing maps a classified document type -> the config whose ``doc_type`` matches. That
    mapping is read from the live table, so editing a config's ``doc_type`` would silently re-route an
    already-published agent to a different Field Configuration.
    """
    return (frozen_spec_for_name(name) or {}).get("doc_type")


async def resolve_config_id(session, name: str | None) -> UUID:
    """``name`` -> config id, preferring this run's frozen mapping.

    Used by the ``AI Field Extractor`` canvas component (the native engine), which resolves the config in
    its own session and never sees ``cfg``. Falls back to the component's historical unscoped lookup so a
    draft run behaves exactly as before.
    """
    frozen = frozen_id_for_name(name)
    if frozen is not None:
        return frozen

    from agentcore.services.database.models.idp.config import IdpFieldConfiguration

    config = (
        await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.name == name,
                IdpFieldConfiguration.deleted_at.is_(None),
            )
        )
    ).first()
    if not config:
        raise FieldConfigMissing(f"Active field configuration '{name}' not found.")
    return config.id


def _rows(spec: dict, key: str, cols: tuple[str, ...]) -> list[SimpleNamespace]:
    """Rehydrate frozen rows into objects the prompt builders can read.

    They are duck-typed (``build_extraction_prompt(headers: List[Any], ...)`` reads ``.field_name`` /
    ``.field_type`` / ``.prompt`` / ``.description`` / ``.column_name`` / ``.column_type``), so plain
    namespaces stand in for the ORM rows.
    """
    out = [
        SimpleNamespace(**{c: raw.get(c) for c in cols})
        for raw in (spec.get(key) or [])
        if isinstance(raw, dict)
    ]
    out.sort(key=lambda r: r.display_order if isinstance(r.display_order, int) else 0)
    return out


async def load_field_definitions(session, config_id) -> tuple[list, list]:
    """``(headers, line_items)`` for ``config_id`` — from this run's frozen set, else from the DB.

    When frozen, the live config's existence is NOT checked: the whole point is that a published run keeps
    working (and keeps extracting the same fields) after the config is edited, renamed, or deleted.
    """
    spec = _current()["by_id"].get(str(config_id))
    if spec is not None:
        return _rows(spec, "headers", _HEADER_COLS), _rows(spec, "line_items", _LINE_COLS)

    from agentcore.services.database.models.idp.config import (
        IdpFieldConfigHeader,
        IdpFieldConfigLineItem,
        IdpFieldConfiguration,
    )

    config = await session.get(IdpFieldConfiguration, config_id)
    if not config or getattr(config, "deleted_at", None) is not None:
        raise FieldConfigMissing(f"Active field configuration '{config_id}' not found.")
    headers = (
        await session.exec(
            select(IdpFieldConfigHeader)
            .where(IdpFieldConfigHeader.config_id == config_id)
            .order_by(IdpFieldConfigHeader.display_order)
        )
    ).all()
    line_items = (
        await session.exec(
            select(IdpFieldConfigLineItem)
            .where(IdpFieldConfigLineItem.config_id == config_id)
            .order_by(IdpFieldConfigLineItem.display_order)
        )
    ).all()
    return list(headers), list(line_items)


# ───────────────────────────── publish-side dump ─────────────────────────────
async def dump_field_definitions(session, config_id) -> dict:
    """Serialize a config's identity + definitions for the publish snapshot. Raises if it can't be read."""
    from agentcore.services.database.models.idp.config import IdpFieldConfiguration

    headers, line_items = await load_field_definitions(session, config_id)
    config = await session.get(IdpFieldConfiguration, config_id)
    return {
        "id": str(config_id),
        "name": getattr(config, "name", None),
        # Frozen so multi-type routing keeps picking the same config after someone edits a doc_type.
        "doc_type": getattr(config, "doc_type", None),
        "headers": [{c: _jsonable(getattr(h, c, None)) for c in _HEADER_COLS} for h in headers],
        "line_items": [{c: _jsonable(getattr(li, c, None)) for c in _LINE_COLS} for li in line_items],
    }


def _jsonable(value):
    return str(value) if isinstance(value, UUID) else value
