"""An IDP document must run the PUBLISHED deployment snapshot, not the draft canvas.

Before this, ``pipeline._run`` did ``session.get(Agent, ...)`` and executed ``agent.data`` — the live
draft. Publishing v1 and then editing the canvas meant the "published" run executed the unpublished
edits. These tests pin the contract:

  * ``resolve_agent_graph`` matches chat's ``_resolve_agent_data_for_env`` exactly (parity).
  * A published run reads the frozen snapshot, even after the draft is mutated.
  * A published run NEVER writes back to ``agent.data`` (the ORM-clobber hazard).
  * No env -> the draft (the Playground path stays unchanged).
  * A missing published version fails loudly instead of silently falling back to the draft.
  * Split children inherit their parent's run target.
  * ``IdpAgent`` settings are read from the snapshot's pinned ``_idp_agent_config``.
"""

from uuid import uuid4

import fitz
import pytest
from sqlmodel import select

from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.agent_deployment_prod.model import (
    AgentDeploymentProd,
    DeploymentPRODStatusEnum,
)
from agentcore.services.database.models.agent_deployment_uat.model import (
    AgentDeploymentUAT,
    DeploymentUATStatusEnum,
)
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import IdpDocument
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user.model import User
from agentcore.services.deps import session_scope
from agentcore.services.idp.snapshot import (
    DEV,
    PROD,
    UAT,
    IDP_CONFIG_KEY,
    SnapshotError,
    normalize_env,
    pinned_idp_config,
    resolve_agent_graph,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ───────────────────────────── graph helpers ─────────────────────────────
def _node(display_name: str, fields: dict | None = None) -> dict:
    return {
        "id": display_name.replace(" ", ""),
        "data": {
            "type": display_name.replace(" ", ""),
            "node": {
                "display_name": display_name,
                "template": {k: {"value": v} for k, v in (fields or {}).items()},
            },
        },
    }


def _graph(marker: str) -> dict:
    """An IDP graph whose extractor prompt carries `marker`, so we can tell versions apart."""
    return {
        "nodes": [
            _node("AI Field Extractor", {"extraction_mode": "field_configuration", "prompt": marker}),
            _node("Processed Docs Output"),
        ],
        "edges": [],
        "_marker": marker,
    }


def _marker(graph: dict) -> str:
    return graph.get("_marker")


# ───────────────────────────── DB fixtures ─────────────────────────────
async def _existing_org_and_user(session):
    """Deployment rows have NOT NULL FKs to organization + user, so reuse whatever the dev DB has."""
    user = (await session.exec(select(User).limit(1))).first()
    org = (await session.exec(select(Organization).limit(1))).first()
    if user is None or org is None:
        pytest.skip("no Organization/User in the dev DB to hang a deployment row off")
    return org, user


async def _seed(*, uat: dict[str, tuple[dict, bool]] | None = None,
                prod: dict[str, tuple[dict, bool]] | None = None,
                draft: dict | None = None):
    """Create an Agent (+IdpAgent) and the requested {version_number: (snapshot, is_active)} rows."""
    agent_id = uuid4()
    async with session_scope() as session:
        org, user = await _existing_org_and_user(session)
        session.add(Agent(id=agent_id, name=f"snap-{agent_id.hex[:8]}", data=draft or _graph("draft")))
        await session.flush()
        session.add(IdpAgent(agent_id=agent_id, extraction_mode="dynamic_prompting", is_active=True))

        for vnum, (snap, active) in (uat or {}).items():
            session.add(AgentDeploymentUAT(
                agent_id=agent_id, org_id=org.id, version_number=int(vnum), agent_snapshot=snap,
                agent_name="snap-test", is_active=active, deployed_by=user.id,
                status=DeploymentUATStatusEnum.PUBLISHED,
            ))
        for vnum, (snap, active) in (prod or {}).items():
            session.add(AgentDeploymentProd(
                agent_id=agent_id, org_id=org.id, version_number=int(vnum), agent_snapshot=snap,
                agent_name="snap-test", is_active=active, deployed_by=user.id,
                status=DeploymentPRODStatusEnum.PUBLISHED,
            ))
        await session.commit()
    return agent_id


async def _cleanup(agent_id):
    async with session_scope() as session:
        for model in (AgentDeploymentUAT, AgentDeploymentProd):
            for row in (await session.exec(select(model).where(model.agent_id == agent_id))).all():
                await session.delete(row)
        for ia in (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).all():
            for doc in (await session.exec(
                select(IdpDocument).where(IdpDocument.agent_id == ia.id)
            )).all():
                await session.delete(doc)
            await session.delete(ia)
        agent = await session.get(Agent, agent_id)
        if agent:
            await session.delete(agent)
        await session.commit()


# ───────────────────────────── pure unit tests ─────────────────────────────
def test_normalize_env_accepts_names_numbers_and_junk():
    assert normalize_env("uat") == UAT
    assert normalize_env("UAT") == UAT
    assert normalize_env(" Prod ") == PROD
    assert normalize_env("1") == UAT          # the /api/run numeric encoding
    assert normalize_env(2) == PROD
    assert normalize_env("0") == DEV
    # Anything unknown/blank falls back to the draft — the Playground & legacy rows depend on this.
    assert normalize_env(None) == DEV
    assert normalize_env("") == DEV
    assert normalize_env("staging") == DEV


@pytest.mark.anyio
async def test_bad_version_string_raises_snapshot_error_not_valueerror():
    """Chat's `int(version.lstrip("v"))` raises an uncaught ValueError (HTTP 500) on these."""
    agent_id = await _seed(uat={"1": (_graph("v1"), True)})
    try:
        for bad in ("", "latest", "1.0", "v", "abc"):
            with pytest.raises(SnapshotError):
                async with session_scope() as session:
                    await resolve_agent_graph(session, agent_id, UAT, bad)
    finally:
        await _cleanup(agent_id)


def test_pinned_idp_config_is_empty_for_unpinned_graphs():
    assert pinned_idp_config(None) == {}
    assert pinned_idp_config({}) == {}
    assert pinned_idp_config(_graph("v1")) == {}
    assert pinned_idp_config({IDP_CONFIG_KEY: {"extraction_mode": "named_config"}}) == {
        "extraction_mode": "named_config"
    }
    # A non-dict value must not blow up the run.
    assert pinned_idp_config({IDP_CONFIG_KEY: "garbage"}) == {}


# ───────────────────────────── resolution ─────────────────────────────
@pytest.mark.anyio
async def test_dev_resolves_the_draft_and_ignores_version():
    agent_id = await _seed(draft=_graph("draft"), uat={"1": (_graph("uat-v1"), True)})
    try:
        async with session_scope() as session:
            graph, dep_id = await resolve_agent_graph(session, agent_id, DEV, "v1")
            assert _marker(graph) == "draft"
            assert dep_id is None
            # No env at all (the Playground) → also the draft.
            graph, dep_id = await resolve_agent_graph(session, agent_id, None)
            assert _marker(graph) == "draft"
            assert dep_id is None
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_explicit_version_wins_over_active_and_ignores_is_active():
    """v1 is superseded (is_active=False) but still PUBLISHED → still runnable (shadow deployments)."""
    agent_id = await _seed(uat={"1": (_graph("uat-v1"), False), "2": (_graph("uat-v2"), True)})
    try:
        async with session_scope() as session:
            graph, dep_id = await resolve_agent_graph(session, agent_id, UAT, "v1")
            assert _marker(graph) == "uat-v1"
            assert dep_id is not None
            # No version → the active one.
            graph, _ = await resolve_agent_graph(session, agent_id, UAT, None)
            assert _marker(graph) == "uat-v2"
            # Bare "2" (no leading v) is accepted too.
            graph, _ = await resolve_agent_graph(session, agent_id, UAT, "2")
            assert _marker(graph) == "uat-v2"
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_uat_and_prod_are_separate_tables():
    agent_id = await _seed(uat={"1": (_graph("uat-v1"), True)}, prod={"1": (_graph("prod-v1"), True)})
    try:
        async with session_scope() as session:
            uat_graph, _ = await resolve_agent_graph(session, agent_id, UAT, "v1")
            prod_graph, _ = await resolve_agent_graph(session, agent_id, PROD, "v1")
            assert _marker(uat_graph) == "uat-v1"
            assert _marker(prod_graph) == "prod-v1"
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_missing_published_version_fails_loudly_never_falls_back_to_draft():
    agent_id = await _seed(draft=_graph("draft"), uat={"1": (_graph("uat-v1"), True)})
    try:
        async with session_scope() as session:
            with pytest.raises(SnapshotError):      # no PROD deployment at all
                await resolve_agent_graph(session, agent_id, PROD, None)
            with pytest.raises(SnapshotError):      # UAT exists, but not v9
                await resolve_agent_graph(session, agent_id, UAT, "v9")
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_snapshot_wins_over_a_mutated_draft():
    """Publish v1, then edit the canvas. The published run must still see v1."""
    agent_id = await _seed(draft=_graph("draft-v1"), uat={"1": (_graph("uat-v1"), True)})
    try:
        async with session_scope() as session:          # the developer keeps editing
            agent = await session.get(Agent, agent_id)
            agent.data = _graph("draft-edited-after-publish")
            session.add(agent)
            await session.commit()

        async with session_scope() as session:
            graph, _ = await resolve_agent_graph(session, agent_id, UAT, "v1")
            assert _marker(graph) == "uat-v1"
            draft, _ = await resolve_agent_graph(session, agent_id, DEV)
            assert _marker(draft) == "draft-edited-after-publish"
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_published_run_never_clobbers_the_draft_canvas():
    """The ORM-clobber hazard: `_run` commits several times with the Agent row attached.

    Resolving a snapshot must not mark `agent.data` dirty — otherwise the next commit silently
    overwrites the developer's draft with the published graph.
    """
    from types import SimpleNamespace

    agent_id = await _seed(draft=_graph("draft"), uat={"1": (_graph("uat-v1"), True)})
    try:
        async with session_scope() as session:
            base_agent = await session.get(Agent, agent_id)       # exactly what `_run` does
            graph, _ = await resolve_agent_graph(session, base_agent.id, UAT, "v1")

            # `_run` builds a NON-ORM shim; assigning base_agent.data would be the bug.
            graph_agent = SimpleNamespace(
                id=base_agent.id, name=base_agent.name,
                org_id=base_agent.org_id, dept_id=base_agent.dept_id, data=graph,
            )
            assert _marker(graph_agent.data) == "uat-v1"
            assert base_agent not in session.dirty, "resolving a snapshot dirtied the Agent row"
            await session.commit()                                 # `_run` commits mid-pipeline

        async with session_scope() as session:                     # re-read from the DB
            agent = await session.get(Agent, agent_id)
            assert _marker(agent.data) == "draft", "the published run overwrote the draft canvas!"
    finally:
        await _cleanup(agent_id)


# ───────────────────────────── auditability: prove the source ─────────────────────────────
def test_graph_fingerprint_ignores_publish_keys_and_key_order():
    """It must answer "is the executed graph the canvas graph?", not "are these dicts byte-equal?"."""
    from agentcore.services.idp.snapshot import graph_fingerprint

    draft = _graph("v1")
    published = dict(draft)
    published["_input_type"] = "autonomous"          # added by publish
    published[IDP_CONFIG_KEY] = {"extraction_mode": "named_config"}
    assert graph_fingerprint(published) == graph_fingerprint(draft), "publish-time keys changed the digest"

    reordered = {"edges": draft["edges"], "nodes": draft["nodes"], "_marker": draft["_marker"]}
    assert graph_fingerprint(reordered) == graph_fingerprint(draft)

    assert graph_fingerprint(_graph("v2")) != graph_fingerprint(draft)   # a real change does move it
    assert graph_fingerprint(None) == graph_fingerprint({})


@pytest.mark.anyio
async def test_resolver_reports_the_table_and_row_it_read():
    """`doc.run_env` echoed back proves nothing. The ORM row's table + primary key does."""
    from agentcore.services.idp.snapshot import graph_fingerprint, resolve_agent_graph_with_source

    agent_id = await _seed(
        draft=_graph("draft"),
        uat={"2": (_graph("uat-v2"), True)},
        prod={"1": (_graph("prod-v1"), True)},
    )
    try:
        async with session_scope() as session:
            graph, src = await resolve_agent_graph_with_source(session, agent_id, UAT, "v2")
            assert src.table == "agent_deployment_uat"
            assert src.deployment_id is not None
            assert (src.env, src.version_number, src.is_active) == ("uat", 2, True)
            assert src.fingerprint == graph_fingerprint(graph)
            assert src.node_count == len(graph["nodes"])
            assert "agent_deployment_uat" in src.describe() and str(src.deployment_id) in src.describe()

            _g, src = await resolve_agent_graph_with_source(session, agent_id, PROD, None)
            assert src.table == "agent_deployment_prod" and src.version_number == 1

            # The draft is the ONLY case with no deployment row.
            draft_graph, src = await resolve_agent_graph_with_source(session, agent_id, DEV)
            assert src.table == "agent" and src.deployment_id is None and src.version_number is None
            assert "DRAFT canvas" in src.describe()

            # ...and its fingerprint differs from the published one, which is what the log asserts.
            assert graph_fingerprint(draft_graph) != graph_fingerprint(graph)
    finally:
        await _cleanup(agent_id)


def test_run_logs_the_graph_source_and_compares_it_to_the_draft():
    """The audit trail is only useful if `_run` actually emits it."""
    import inspect

    from agentcore.services.idp import pipeline

    src = inspect.getsource(pipeline._run)
    assert "resolve_agent_graph_with_source(" in src, "_run no longer records where the graph came from"
    assert "graph_source.describe()" in src
    assert "graph_fingerprint(getattr(base_agent, \"data\", None))" in src, "the draft is not compared"
    # The flow log (what the user downloads) must carry the table + row, not just the env.
    assert '"table": graph_source.table' in src
    assert '"deployment_id"' in src


# ───────────────────────────── parity with chat ─────────────────────────────
@pytest.mark.anyio
async def test_parity_with_chat_resolver():
    """`resolve_agent_graph` must return exactly what chat's `_resolve_agent_data_for_env` returns."""
    from agentcore.api.endpoints import RunEnvironment, _resolve_agent_data_for_env

    agent_id = await _seed(
        draft=_graph("draft"),
        uat={"1": (_graph("uat-v1"), False), "2": (_graph("uat-v2"), True)},
        prod={"1": (_graph("prod-v1"), True)},
    )
    try:
        cases = [
            (RunEnvironment.DEV, DEV, None),
            (RunEnvironment.UAT, UAT, "v1"),   # superseded-but-published
            (RunEnvironment.UAT, UAT, "v2"),
            (RunEnvironment.UAT, UAT, None),   # latest active
            (RunEnvironment.PROD, PROD, "v1"),
            (RunEnvironment.PROD, PROD, None),
        ]
        for chat_env, our_env, version in cases:
            chat_graph, _, _ = await _resolve_agent_data_for_env(agent_id, chat_env, version)
            async with session_scope() as session:
                ours, _ = await resolve_agent_graph(session, agent_id, our_env, version)
            assert _marker(ours) == _marker(chat_graph), f"parity broke for {our_env}/{version}"
    finally:
        await _cleanup(agent_id)


# ───────────────────────────── pinned IdpAgent config ─────────────────────────────
@pytest.mark.anyio
async def test_pipeline_reads_idp_settings_from_the_snapshot_not_the_live_row(monkeypatch):
    """`_pin_idp_agent` must shadow the live IdpAgent row, whose settings track the DRAFT graph."""
    from agentcore.services.idp.snapshot import pin_idp_agent

    pinned_graph = dict(_graph("uat-v1"))
    field_config_id = uuid4()
    pinned_graph[IDP_CONFIG_KEY] = {
        "extra": {"execution_engine": "native", "ocr_language": "de", "confidence_threshold": 0.9},
        "extraction_mode": "named_config",
        "dynamic_prompt": "pinned prompt",
        "field_config_id": str(field_config_id),
        "default_rule_action": "auto_approve",
    }

    agent_id = await _seed(uat={"1": (pinned_graph, True)})
    try:
        async with session_scope() as session:
            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            live.extra = {"execution_engine": "fixed", "ocr_language": "en", "long_doc_max_pages": 3}
            live.dynamic_prompt = "draft prompt"
            session.add(live)
            await session.commit()

            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            view = pin_idp_agent(live, pinned_graph)

            # Settings come from the snapshot…
            assert view.extraction_mode == "named_config"
            assert view.dynamic_prompt == "pinned prompt"
            assert view.field_config_id == field_config_id
            assert view.default_rule_action == "auto_approve"
            assert view.extra["execution_engine"] == "native"
            assert view.extra["ocr_language"] == "de"
            # …while un-pinned keys still fall through to the live row (merge, not replace).
            assert view.extra["long_doc_max_pages"] == 3
            # Identity always comes from the real row.
            assert view.id == live.id and view.agent_id == live.agent_id

            # Engine precedence: IDP_EXECUTION_ENGINE > pinned snapshot extra > live IdpAgent.extra.
            from agentcore.services.idp.pipeline import _idp_engine_is_native

            monkeypatch.delenv("IDP_EXECUTION_ENGINE", raising=False)
            assert _idp_engine_is_native(view) is True     # from the snapshot
            assert _idp_engine_is_native(live) is False    # the draft says "fixed"

            monkeypatch.setenv("IDP_EXECUTION_ENGINE", "fixed")
            assert _idp_engine_is_native(view) is False    # deployment-wide override still wins
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_resolve_pipeline_config_accepts_the_pinned_shims_end_to_end():
    """The shims must satisfy EVERY attribute `resolve_pipeline_config` reads — not just the pinned ones.

    Regression: `pin_idp_agent` used to return a SimpleNamespace built from an enumerated field list, so
    `resolve_pipeline_config`'s `bool(idp_agent.multi_doc_split)` blew up with AttributeError on the first
    document run against a snapshot that carried `_idp_agent_config`. Poking at individual attributes
    can't catch that — only calling the real consumer can.
    """
    from agentcore.services.idp.agent_config import resolve_pipeline_config
    from agentcore.services.idp.snapshot import graph_agent_view, pin_idp_agent

    pinned_graph = dict(_graph("uat-v1"))
    pinned_graph[IDP_CONFIG_KEY] = {
        "extra": {"execution_engine": "native"},
        "extraction_mode": "named_config",
        "dynamic_prompt": "pinned",
        "field_config_id": None,
        "default_rule_action": "auto_approve",
    }
    agent_id = await _seed(uat={"1": (pinned_graph, True)})
    try:
        async with session_scope() as session:
            agent = await session.get(Agent, agent_id)
            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()

            cfg = await resolve_pipeline_config(
                session, pin_idp_agent(live, pinned_graph), graph_agent_view(agent, pinned_graph)
            )
            assert cfg.extraction_mode == "named_config"
            assert cfg.default_rule_action == "auto_approve"
            # Read through to the live row for anything the snapshot didn't pin.
            assert cfg.multi_doc_split == bool(live.multi_doc_split)
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_shims_read_through_for_unpinned_attributes():
    """Anything not explicitly overridden comes from the wrapped row, and writes never touch it."""
    from agentcore.services.idp.snapshot import graph_agent_view, pin_idp_agent

    pinned_graph = dict(_graph("uat-v1"))
    pinned_graph[IDP_CONFIG_KEY] = {"extraction_mode": "named_config"}
    agent_id = await _seed(uat={"1": (pinned_graph, True)})
    try:
        async with session_scope() as session:
            agent = await session.get(Agent, agent_id)
            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()

            view = pin_idp_agent(live, pinned_graph)
            assert view.extraction_mode == "named_config"        # pinned
            # Every other IdpAgent column reads through — enumerate them so a new column is covered too.
            for col in ("id", "agent_id", "multi_doc_split", "is_active", "auto_stop_after_batch",
                        "preprocessing_steps", "created_at", "deleted_at"):
                assert getattr(view, col) == getattr(live, col), f"{col} did not read through"
            assert getattr(view, "definitely_not_a_column", "fallback") == "fallback"

            gview = graph_agent_view(agent, pinned_graph)
            assert gview.data is pinned_graph                     # overridden
            assert (gview.id, gview.name, gview.org_id) == (agent.id, agent.name, agent.org_id)

            # A write lands on the overlay, NOT on the ORM rows (the clobber guard).
            view.extraction_mode = "SENTINEL"
            gview.data = {"nodes": ["SENTINEL"]}
            assert view.extraction_mode == "SENTINEL"
            assert live.extraction_mode == "dynamic_prompting", "overlay write leaked onto IdpAgent"
            assert agent.data != {"nodes": ["SENTINEL"]}, "overlay write leaked onto Agent.data"
            assert live not in session.dirty and agent not in session.dirty
    finally:
        await _cleanup(agent_id)


@pytest.mark.anyio
async def test_pin_returns_the_live_row_untouched_for_unpinned_graphs():
    """Back-compat: dev runs and snapshots published before `_idp_agent_config` existed."""
    from agentcore.services.idp.snapshot import pin_idp_agent

    agent_id = await _seed(uat={"1": (_graph("uat-v1"), True)})
    try:
        async with session_scope() as session:
            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            assert pin_idp_agent(live, _graph("uat-v1")) is live
            assert pin_idp_agent(live, {}) is live
    finally:
        await _cleanup(agent_id)


# ───────────────────────────── no draft leaks downstream of _run ─────────────────────────────
def test_no_service_reresolves_the_draft_agent_behind_runs_back():
    """Guard against a second, hidden `session.get(Agent, ...)` re-reading the draft mid-run.

    `_run` resolves the snapshot once and passes shims down. Any service that fetches the Agent row
    itself must resolve the run's graph from `doc.run_env`/`doc.run_version` — otherwise a published
    run silently picks up the developer's current canvas. `classification.py` did exactly that.
    """
    import pathlib
    import re

    idp = pathlib.Path(__file__).resolve().parents[1] / "base" / "agentcore" / "services" / "idp"
    offenders = []
    for path in idp.rglob("*.py"):
        if path.name == "snapshot.py":     # the one place allowed to read `Agent.data` for `dev`
            continue
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"session\.get\(\s*Agent\s*,|select\(\s*Agent\s*\)", src):
            line = src[: match.start()].count("\n") + 1
            # Fetching the Agent row is fine; deriving the GRAPH from it without resolving is not.
            if "resolve_agent_graph" not in src:
                offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        f"these modules fetch the Agent row but never call resolve_agent_graph, so they read the "
        f"DRAFT graph even on a published run: {offenders}"
    )


@pytest.mark.anyio
async def test_classification_resolves_the_documents_published_graph():
    """`classify_and_persist` runs in its own session and used to re-read the draft `agent.data`."""
    import inspect

    from agentcore.services.idp import classification

    src = inspect.getsource(classification.classify_and_persist)
    assert "resolve_agent_graph" in src, "classification re-reads the draft graph"
    assert "doc.run_env" in src, "classification ignores the document's run target"
    # It must never hand the raw ORM Agent to resolve_pipeline_config (that's the draft graph).
    assert "resolve_pipeline_config(session, idp_agent, base_agent)" not in src


def test_published_runs_do_not_fall_back_to_the_draft_synced_rules_table():
    """`idp_agent_rules` is rewritten from the draft canvas on every save (_sync_canvas_rules)."""
    import inspect

    from agentcore.services.idp import pipeline

    src = inspect.getsource(pipeline._route)
    table_read = src.index("select(IdpAgentRule)")
    guard = src.index('getattr(cfg, "environment", "dev") == "dev"')
    assert guard < table_read, "the rules-table fallback is not gated on a draft run"


# ───────────────────────────── publish freezes the config ─────────────────────────────
@pytest.mark.anyio
async def test_publish_freezes_idp_config_and_is_idempotent():
    from agentcore.api.publish import _freeze_idp_agent_config

    agent_id = await _seed()
    try:
        async with session_scope() as session:
            live = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            live.extra = {"execution_engine": "native", "ocr_language": "fr"}
            session.add(live)
            await session.commit()

            snapshot = _graph("uat-v1")
            await _freeze_idp_agent_config(snapshot, session, agent_id)
            pinned = pinned_idp_config(snapshot)
            assert pinned["extra"]["execution_engine"] == "native"
            assert pinned["extra"]["ocr_language"] == "fr"
            assert pinned["extraction_mode"] == "dynamic_prompting"

            # A PROD promotion re-runs this on the UAT snapshot: it must keep what UAT tested.
            snapshot[IDP_CONFIG_KEY]["extra"]["ocr_language"] = "de"
            await _freeze_idp_agent_config(snapshot, session, agent_id)
            assert pinned_idp_config(snapshot)["extra"]["ocr_language"] == "de"

            # Non-IDP graphs are never touched.
            plain = {"nodes": [_node("Chat Input")], "edges": []}
            await _freeze_idp_agent_config(plain, session, agent_id)
            assert IDP_CONFIG_KEY not in plain
    finally:
        await _cleanup(agent_id)


# ───────────────────────────── split children ─────────────────────────────
class _MockStorage:
    def __init__(self, file_bytes):
        self.file_bytes = file_bytes
        self.saved_files = {}

    async def get_file(self, agent_id: str, file_name: str) -> bytes:
        return self.file_bytes

    async def save_file(self, agent_id: str, file_name: str, data: bytes) -> None:
        self.saved_files[file_name] = data


@pytest.mark.anyio
async def test_split_children_inherit_the_parents_run_target():
    """A child is re-enqueued through the full pipeline — it must run the SAME agent version."""
    from agentcore.services.idp.splitting import materialize_children

    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((50, 50), f"Page {i + 1}")
    pdf_bytes = doc.tobytes()
    doc.close()

    agent_id = await _seed(prod={"2": (_graph("prod-v2"), True)})
    parent_doc_id = uuid4()
    try:
        async with session_scope() as session:
            idp_agent = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            session.add(IdpDocument(
                id=parent_doc_id, agent_id=idp_agent.id, original_filename="parent.pdf",
                file_path="mock/parent.pdf", file_type="pdf", file_size_bytes=len(pdf_bytes),
                page_count=3, source="upload", status="extracted",
                run_env="prod", run_version="v2",
            ))
            await session.commit()

        async with session_scope() as session:
            parent = await session.get(IdpDocument, parent_doc_id)
            child_ids = await materialize_children(session, _MockStorage(pdf_bytes), parent, [(0, 0), (1, 2)])
        assert len(child_ids) == 2

        async with session_scope() as session:
            children = (await session.exec(
                select(IdpDocument).where(IdpDocument.parent_document_id == parent_doc_id)
            )).all()
            assert len(children) == 2
            for child in children:
                assert child.run_env == "prod", "split child fell back to the draft graph"
                assert child.run_version == "v2"
    finally:
        async with session_scope() as session:
            for child in (await session.exec(
                select(IdpDocument).where(IdpDocument.parent_document_id == parent_doc_id)
            )).all():
                await session.delete(child)
            parent = await session.get(IdpDocument, parent_doc_id)
            if parent:
                await session.delete(parent)
            await session.commit()
        await _cleanup(agent_id)
