"""A published run must extract the field set that was frozen at publish — not today's config.

``idp_field_configurations`` has no version column: it is mutated in place, and the graph snapshot stores
only the config's NAME. So before this, editing "Invoice" changed what every already-published UAT/PROD
agent extracted, and deleting it silently fell through to a GLOBAL catalogue config of the same name.

Publish now freezes the resolved definitions into ``_idp_agent_config.field_configs`` and the run reads
them from a task-scoped ContextVar (``services/idp/field_defs``). The draft still reads the live tables,
so editing a config keeps giving instant feedback on the canvas.
"""

from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from agentcore.services.database.models.idp.config import (
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
    IdpFieldConfiguration,
)
from agentcore.services.deps import session_scope
from agentcore.services.idp.field_defs import (
    FIELD_CONFIGS_KEY,
    FieldConfigMissing,
    dump_field_definitions,
    frozen_doc_type,
    frozen_id_for_name,
    has_frozen_defs,
    install_for_graph,
    load_field_definitions,
    resolve_config_id,
    use_frozen_field_defs,
)
from agentcore.services.idp.snapshot import IDP_CONFIG_KEY


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ───────────────────────────── fixtures ─────────────────────────────
async def _make_config(name: str, field_names: list[str], *, doc_type: str | None = None,
                       columns: list[str] | None = None) -> UUID:
    config_id = uuid4()
    async with session_scope() as session:
        session.add(IdpFieldConfiguration(id=config_id, name=name, doc_type=doc_type, is_active=True))
        await session.flush()
        for i, fn in enumerate(field_names):
            session.add(IdpFieldConfigHeader(
                config_id=config_id, field_name=fn, field_type="text",
                display_order=i, description=f"desc of {fn}", prompt=f"prompt for {fn}",
            ))
        for i, cn in enumerate(columns or []):
            session.add(IdpFieldConfigLineItem(
                config_id=config_id, column_name=cn, column_type="text", display_order=i,
                prompt=f"prompt for {cn}",
            ))
        await session.commit()
    return config_id


async def _drop_config(config_id):
    async with session_scope() as session:
        for model in (IdpFieldConfigHeader, IdpFieldConfigLineItem):
            for row in (await session.exec(select(model).where(model.config_id == config_id))).all():
                await session.delete(row)
        cfg = await session.get(IdpFieldConfiguration, config_id)
        if cfg:
            await session.delete(cfg)
        await session.commit()


async def _add_header(config_id, field_name: str):
    async with session_scope() as session:
        session.add(IdpFieldConfigHeader(
            config_id=config_id, field_name=field_name, field_type="text", display_order=99,
        ))
        await session.commit()


def _graph_with(frozen: dict) -> dict:
    return {"nodes": [], "edges": [], IDP_CONFIG_KEY: {FIELD_CONFIGS_KEY: frozen}}


# ───────────────────────────── the core guarantee ─────────────────────────────
@pytest.mark.anyio
async def test_editing_a_config_does_not_change_a_published_run():
    """THE requirement: publish with 2 fields, add a 3rd, the published run still extracts 2."""
    config_id = await _make_config(f"Inv-{uuid4().hex[:8]}", ["invoice_no", "total"])
    try:
        # Publish freezes what exists now.
        async with session_scope() as session:
            frozen = {"Invoice": await dump_field_definitions(session, config_id)}
        assert [h["field_name"] for h in frozen["Invoice"]["headers"]] == ["invoice_no", "total"]

        # Someone edits the config afterwards.
        await _add_header(config_id, "tax_amount")

        # The DRAFT run sees the edit...
        async with session_scope() as session:
            install_for_graph({"nodes": []})            # draft graph -> empty frozen set
            headers, _ = await load_field_definitions(session, config_id)
            assert [h.field_name for h in headers] == ["invoice_no", "total", "tax_amount"]

        # ...the PUBLISHED run does not.
        async with session_scope() as session:
            install_for_graph(_graph_with(frozen))
            headers, _ = await load_field_definitions(session, config_id)
            assert [h.field_name for h in headers] == ["invoice_no", "total"]
    finally:
        await _drop_config(config_id)


@pytest.mark.anyio
async def test_published_run_survives_the_config_being_deleted():
    """A frozen run must not depend on the live row existing at all."""
    config_id = await _make_config(f"Gone-{uuid4().hex[:8]}", ["a", "b"])
    async with session_scope() as session:
        frozen = {"Gone": await dump_field_definitions(session, config_id)}
    await _drop_config(config_id)

    # Live lookup now fails...
    async with session_scope() as session:
        install_for_graph({"nodes": []})
        with pytest.raises(FieldConfigMissing):
            await load_field_definitions(session, config_id)

    # ...but the published run still has its fields.
    async with session_scope() as session:
        install_for_graph(_graph_with(frozen))
        headers, _ = await load_field_definitions(session, config_id)
        assert [h.field_name for h in headers] == ["a", "b"]


@pytest.mark.anyio
async def test_renaming_a_config_does_not_break_a_published_run():
    """The graph stores the NAME; publish froze name -> id, so a rename can't orphan the run."""
    original = f"Ren-{uuid4().hex[:8]}"
    config_id = await _make_config(original, ["x"])
    try:
        async with session_scope() as session:
            frozen = {original: await dump_field_definitions(session, config_id)}

        async with session_scope() as session:      # the rename
            cfg = await session.get(IdpFieldConfiguration, config_id)
            cfg.name = original + "-renamed"
            session.add(cfg)
            await session.commit()

        with use_frozen_field_defs(frozen):
            assert frozen_id_for_name(original) == config_id     # still resolves
        with use_frozen_field_defs({}):
            assert frozen_id_for_name(original) is None          # draft: falls through to live lookup
    finally:
        await _drop_config(config_id)


@pytest.mark.anyio
async def test_frozen_id_beats_the_silent_global_fallback():
    """_lookup_config_id_by_name falls back to a GLOBAL config of the same name. Frozen must win."""
    from agentcore.services.idp.agent_config import _lookup_config_id_by_name

    name = f"Dup-{uuid4().hex[:8]}"
    global_id = await _make_config(name, ["global_field"])   # org_id NULL -> the fallback target
    try:
        async with session_scope() as session:
            # No frozen set -> the live lookup finds the global config.
            install_for_graph({"nodes": []})
            assert await _lookup_config_id_by_name(session, None, name) == global_id

            # Frozen set pins a DIFFERENT id -> the global fallback is never consulted.
            pinned = uuid4()
            with use_frozen_field_defs({name: {"id": str(pinned), "headers": [], "line_items": []}}):
                assert await _lookup_config_id_by_name(session, None, name) == pinned
    finally:
        await _drop_config(global_id)


# ───────────────────────────── multi-type routing ─────────────────────────────
def test_doc_type_routing_uses_the_frozen_mapping():
    """Editing a config's doc_type must not re-route an already-published agent."""
    frozen = {
        "Invoice": {"id": str(uuid4()), "name": "Invoice", "doc_type": "invoice"},
        "Medical Report": {"id": str(uuid4()), "name": "Medical Report", "doc_type": "medical_report"},
    }
    with use_frozen_field_defs(frozen):
        assert has_frozen_defs() is True
        assert frozen_doc_type("Invoice") == "invoice"
        assert frozen_doc_type("invoice") == "invoice"          # case-insensitive name match
        assert frozen_doc_type("Medical Report") == "medical_report"
        assert frozen_doc_type("Unknown") is None
    with use_frozen_field_defs({}):
        assert has_frozen_defs() is False                        # draft -> live doc_type lookup
        assert frozen_doc_type("Invoice") is None


@pytest.mark.anyio
async def test_multi_type_routing_falls_back_to_the_config_name_when_doc_type_is_null():
    """A config with no `doc_type` must still be routable by its NAME.

    Auto-cloned configs never had `doc_type` copied over. Routing on doc_type alone made them
    unselectable, so the extractor returned `extracted={}` and the document saved zero fields — with
    every node reporting "ok". The fixed pipeline already routes on the name (`_build_config_name_map`).
    """
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.schema.message import Message

    ex = IDPLLMExtractor()
    ex.config_names = ["Invoice", "Medical Report"]
    ex.config_name = "Invoice"
    ex.extraction_mode = "field_configuration"

    def _classified(kind: str) -> Message:
        return Message(text="x", additional_kwargs={"idp": {}, "classification": {"type": kind}})

    # doc_type is NULL for Invoice (the real shape of an auto-cloned config) -> match on the name.
    frozen = {
        "Invoice": {"id": str(uuid4()), "name": "Invoice", "doc_type": None},
        "Medical Report": {"id": str(uuid4()), "name": "Medical Report", "doc_type": "Medical Report"},
    }
    with use_frozen_field_defs(frozen):
        assert await ex._resolve_config_name_from_classification(_classified("Invoice")) == "Invoice"
        assert await ex._resolve_config_name_from_classification(_classified("invoice")) == "Invoice"
        # doc_type still wins when it IS set.
        assert await ex._resolve_config_name_from_classification(_classified("medical report")) == "Medical Report"
        # A genuinely unknown type still routes nowhere (the document is skipped, as designed).
        assert await ex._resolve_config_name_from_classification(_classified("bank statement")) is None


# ───────────────────────────── rehydration fidelity ─────────────────────────────
@pytest.mark.anyio
async def test_frozen_rows_carry_every_attribute_the_prompt_builder_reads():
    """The prompt builders are duck-typed; a missing attribute would silently degrade the prompt."""
    from agentcore.services.idp.prompt_templates import build_extraction_messages

    config_id = await _make_config(f"Fid-{uuid4().hex[:8]}", ["amount"], columns=["qty"])
    try:
        async with session_scope() as session:
            frozen = {"Fid": await dump_field_definitions(session, config_id)}
            live_h, live_l = await load_field_definitions(session, config_id)   # ORM rows (no frozen set)

        async with session_scope() as session:
            install_for_graph(_graph_with(frozen))
            froz_h, froz_l = await load_field_definitions(session, config_id)

        for a, b in ((live_h[0], froz_h[0]),):
            assert (a.field_name, a.field_type, a.prompt, a.description) == \
                   (b.field_name, b.field_type, b.prompt, b.description)
        for a, b in ((live_l[0], froz_l[0]),):
            assert (a.column_name, a.column_type, a.prompt) == (b.column_name, b.column_type, b.prompt)

        # Byte-identical prompts from ORM rows and rehydrated rows.
        assert build_extraction_messages(live_h, live_l, "TEXT") == build_extraction_messages(froz_h, froz_l, "TEXT")
    finally:
        await _drop_config(config_id)


@pytest.mark.anyio
async def test_frozen_rows_are_ordered_by_display_order_not_json_order():
    config_id = uuid4()
    frozen = {"X": {"id": str(config_id), "headers": [
        {"field_name": "third", "display_order": 2},
        {"field_name": "first", "display_order": 0},
        {"field_name": "second", "display_order": 1},
    ], "line_items": []}}
    async with session_scope() as session:
        install_for_graph(_graph_with(frozen))
        headers, _ = await load_field_definitions(session, config_id)
    assert [h.field_name for h in headers] == ["first", "second", "third"]


# ───────────────────────────── isolation + back-compat ─────────────────────────────
@pytest.mark.anyio
async def test_install_for_graph_clears_a_previous_runs_frozen_set():
    """`_run` calls install_for_graph unconditionally, so a draft run after a published one is clean."""
    install_for_graph(_graph_with({"A": {"id": str(uuid4()), "headers": [], "line_items": []}}))
    assert has_frozen_defs() is True
    install_for_graph({"nodes": [], "edges": []})       # a draft graph
    assert has_frozen_defs() is False


@pytest.mark.anyio
async def test_concurrent_documents_do_not_see_each_others_frozen_defs():
    """The set is task-scoped: two documents processed concurrently must not cross-contaminate."""
    import asyncio

    async def run(marker):
        install_for_graph(_graph_with({marker: {"id": str(uuid4()), "headers": [], "line_items": []}}))
        await asyncio.sleep(0)                          # yield so the tasks interleave
        return frozen_id_for_name(marker) is not None and frozen_id_for_name("other") is None

    install_for_graph({"nodes": []})
    results = await asyncio.gather(run("docA"), run("docB"))
    assert all(results)
    assert has_frozen_defs() is False, "a child task leaked its frozen set into the parent"


@pytest.mark.anyio
async def test_pre_fix_snapshots_fall_back_to_the_live_tables():
    """Snapshots published before field_configs existed must keep working unchanged."""
    config_id = await _make_config(f"Old-{uuid4().hex[:8]}", ["legacy"])
    try:
        async with session_scope() as session:
            install_for_graph({"nodes": [], IDP_CONFIG_KEY: {"extraction_mode": "named_config"}})
            assert has_frozen_defs() is False
            headers, _ = await load_field_definitions(session, config_id)
            assert [h.field_name for h in headers] == ["legacy"]
    finally:
        await _drop_config(config_id)


# ───────────────────────────── the publish side ─────────────────────────────
@pytest.mark.anyio
async def test_publish_freezes_every_config_the_extractor_references():
    """`config_name` AND the multi-type `config_names` list must all be frozen, with their doc_types."""
    from agentcore.api.publish import _freeze_idp_agent_config
    from agentcore.services.database.models.agent.model import Agent
    from agentcore.services.database.models.idp.config import IdpAgent

    inv_name, med_name = f"Inv-{uuid4().hex[:8]}", f"Med-{uuid4().hex[:8]}"
    inv = await _make_config(inv_name, ["invoice_no", "total"], doc_type="invoice", columns=["qty"])
    med = await _make_config(med_name, ["patient"], doc_type="medical_report")
    agent_id = uuid4()
    try:
        async with session_scope() as session:
            session.add(Agent(id=agent_id, name=f"pub-{agent_id.hex[:8]}", data={}))
            await session.flush()
            session.add(IdpAgent(agent_id=agent_id, extraction_mode="named_config", is_active=True))
            await session.commit()

        snapshot = {
            "nodes": [{
                "id": "DocumentExtractor-1",
                "data": {"type": "DocumentExtractor", "node": {
                    "display_name": "AI Field Extractor",
                    "template": {
                        "config_name": {"value": inv_name},
                        "config_names": {"value": [inv_name, med_name]},   # inv appears twice -> deduped
                    },
                }},
            }],
            "edges": [],
        }

        async with session_scope() as session:
            await _freeze_idp_agent_config(snapshot, session, agent_id, org_id=None)

        frozen = snapshot[IDP_CONFIG_KEY][FIELD_CONFIGS_KEY]
        assert set(frozen) == {inv_name, med_name}
        assert frozen[inv_name]["id"] == str(inv)
        assert frozen[inv_name]["doc_type"] == "invoice"
        assert [h["field_name"] for h in frozen[inv_name]["headers"]] == ["invoice_no", "total"]
        assert [c["column_name"] for c in frozen[inv_name]["line_items"]] == ["qty"]
        assert [h["field_name"] for h in frozen[med_name]["headers"]] == ["patient"]

        # The frozen snapshot is directly usable as this run's set.
        async with session_scope() as session:
            install_for_graph(snapshot)
            assert frozen_id_for_name(inv_name) == inv
            assert frozen_doc_type(med_name) == "medical_report"
            headers, lines = await load_field_definitions(session, inv)
            assert [h.field_name for h in headers] == ["invoice_no", "total"]
            assert [c.column_name for c in lines] == ["qty"]
    finally:
        async with session_scope() as session:
            ia = (await session.exec(select(IdpAgent).where(IdpAgent.agent_id == agent_id))).first()
            if ia:
                await session.delete(ia)
            a = await session.get(Agent, agent_id)
            if a:
                await session.delete(a)
            await session.commit()
        await _drop_config(inv)
        await _drop_config(med)


@pytest.mark.anyio
async def test_publish_does_not_break_when_a_config_name_cannot_be_resolved():
    """A dangling name is skipped with a warning, not a 500 on an otherwise valid publish."""
    from agentcore.api.publish import _freeze_field_configs

    snapshot = {"nodes": [{
        "id": "DocumentExtractor-1",
        "data": {"type": "DocumentExtractor", "node": {
            "display_name": "AI Field Extractor",
            "template": {"config_name": {"value": f"does-not-exist-{uuid4().hex[:8]}"}},
        }},
    }], "edges": []}
    async with session_scope() as session:
        assert await _freeze_field_configs(snapshot, session, None) == {}


@pytest.mark.anyio
async def test_resolve_config_id_prefers_frozen_then_falls_back_live():
    """The native canvas component's name -> id path."""
    name = f"Comp-{uuid4().hex[:8]}"
    config_id = await _make_config(name, ["f"])
    try:
        async with session_scope() as session:
            install_for_graph({"nodes": []})
            assert await resolve_config_id(session, name) == config_id      # live

            pinned = uuid4()
            with use_frozen_field_defs({name: {"id": str(pinned)}}):
                assert await resolve_config_id(session, name) == pinned     # frozen wins

            with pytest.raises(FieldConfigMissing):
                await resolve_config_id(session, "no-such-config-name")
    finally:
        await _drop_config(config_id)
