"""Unit tests for the IDP Outlook Connector Input node — filter building, client-side matching,
provider-aware show/hide, and the trigger-service recipient helper. Pure/fast (no DB / no Graph)."""
import pytest

from agentcore.components.IDP.connector_input import (
    IDPConnectorInput,
    _EMAIL_FIELDS,
    _addr_list,
    _parse_connector_id,
    _parse_connector_provider,
)
from agentcore.services.trigger.service import _recipient_addrs


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _node(**kw):
    n = IDPConnectorInput()
    for f in ["filter_sender", "filter_subject", "filter_body", "filter_to", "filter_cc"]:
        setattr(n, f, "")
    n.filter_importance = "all"
    n.filter_has_attachments = False
    n.unread_only = False
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def test_parse_helpers():
    assert _parse_connector_id("Acme | outlook | a@b.com | uuid-1") == "uuid-1"
    assert _parse_connector_provider("Acme | outlook | a@b.com | uuid-1") == "outlook"
    assert _parse_connector_provider("Store | azure_blob | x | u") == "azure_blob"
    assert _parse_connector_provider("") == ""
    recips = [{"emailAddress": {"address": "a@b.com"}}, {"emailAddress": {"address": "c@d.com"}}]
    assert _addr_list(recips) == ["a@b.com", "c@d.com"]
    assert _recipient_addrs(recips) == ["a@b.com", "c@d.com"]
    assert _recipient_addrs(None) == []


def test_build_filters_odata():
    n = _node(
        filter_sender="x@y.com", filter_subject="invoice", filter_body="urgent",
        filter_to="t@y.com", filter_cc="c@y.com", filter_importance="high",
        filter_has_attachments=True, unread_only=True,
    )
    odata, crit = n._build_filters()
    joined = " ".join(odata)
    assert "hasAttachments eq true" in joined
    assert "isRead eq false" in joined
    assert "from/emailAddress/address eq 'x@y.com'" in joined
    assert "contains(subject, 'invoice')" in joined
    assert "contains(body/content, 'urgent')" in joined
    assert "toRecipients/any(r:r/emailAddress/address eq 't@y.com')" in joined
    assert "ccRecipients/any(c:c/emailAddress/address eq 'c@y.com')" in joined
    assert "importance eq 'high'" in joined


def test_build_filters_escapes_quotes():
    n = _node(filter_subject="o'brien")
    odata, _ = n._build_filters()
    assert "contains(subject, 'o''brien')" in " ".join(odata)


def test_build_filters_empty():
    n = _node()
    odata, crit = n._build_filters()
    assert odata == []  # no filters set, has_attachments False
    assert crit["importance"] == "all"


def test_matches_clientside():
    n = _node(filter_subject="invoice", filter_cc="boss@y.com", filter_has_attachments=True)
    _, crit = n._build_filters()
    ok = {"subject": "Q3 Invoice", "hasAttachments": True,
          "ccRecipients": [{"emailAddress": {"address": "boss@y.com"}}]}
    no_cc = {"subject": "Q3 Invoice", "hasAttachments": True, "ccRecipients": []}
    no_subj = {"subject": "hello", "hasAttachments": True,
               "ccRecipients": [{"emailAddress": {"address": "boss@y.com"}}]}
    no_att = {"subject": "Q3 Invoice", "hasAttachments": False,
              "ccRecipients": [{"emailAddress": {"address": "boss@y.com"}}]}
    assert n._matches(ok, crit) is True
    assert n._matches(no_cc, crit) is False
    assert n._matches(no_subj, crit) is False
    assert n._matches(no_att, crit) is False


def test_matches_recipient_and_unread_and_importance():
    n = _node(filter_to="me@y.com", unread_only=True, filter_importance="high")
    _, crit = n._build_filters()
    ok = {"toRecipients": [{"emailAddress": {"address": "me@y.com"}}], "isRead": False, "importance": "high"}
    read = {"toRecipients": [{"emailAddress": {"address": "me@y.com"}}], "isRead": True, "importance": "high"}
    low = {"toRecipients": [{"emailAddress": {"address": "me@y.com"}}], "isRead": False, "importance": "low"}
    assert n._matches(ok, crit) is True
    assert n._matches(read, crit) is False
    assert n._matches(low, crit) is False


def test_provider_aware_show_hide():
    n = IDPConnectorInput()
    names = [getattr(i, "name") for i in n.inputs]
    bc = {nm: {"show": nm == "connector", "value": ""} for nm in names}
    bc["connector"]["value"] = "X | outlook | e | u"
    out = n.update_build_config(bc, field_value="X | outlook | e | u", field_name="connector")
    assert all(out[f]["show"] for f in _EMAIL_FIELDS), "all email fields shown for outlook"

    bc2 = {nm: {"show": False, "value": ""} for nm in names}
    out2 = n.update_build_config(bc2, field_value="X | azure_blob | e | u", field_name="connector")
    assert not any(out2[f]["show"] for f in _EMAIL_FIELDS), "no email fields for non-outlook"


def test_node_name_unchanged():
    # publish sync + FE dropdown depend on this exact node name.
    assert IDPConnectorInput.name == "IDPConnectorInput"


# ── Canvas connector resolution (connector_name plain-name + connector_provider) ──
# The builder canvas (IDPConnectorDropdown) saves the chosen connector's PLAIN NAME in
# `connector_name` and its provider in `connector_provider`. The component historically read only
# `self.connector` — a "name | provider | target | uuid" token produced by the BACKEND palette
# dropdown. On the canvas `self.connector` is empty, so a manual run failed with "No connector
# selected", and even the plain name is not a UUID (`_get_outlook_config` needs the UUID). These
# cover the resolver that bridges both save shapes and routes the right provider.

def test_resolve_selected_connector_from_canvas_name(monkeypatch):
    """Canvas save shape: connector="", connector_name=<plain name>, connector_provider=<provider>.
    The resolver looks the name up in the catalogue → (uuid, provider)."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "MyOutlook"
    n.connector_provider = "outlook"
    monkeypatch.setattr(
        ci, "_resolve_connector_by_name",
        lambda name, provider=None: ("11111111-1111-1111-1111-111111111111", "outlook") if name == "MyOutlook" else None,
    )
    cid, provider = n._resolve_selected_connector()
    assert cid == "11111111-1111-1111-1111-111111111111"
    assert provider == "outlook"


def test_resolve_selected_connector_from_backend_token():
    """The backend palette still saves the pipe token in `connector` — that path must keep working
    with no catalogue lookup (the uuid is right there in the token)."""
    n = IDPConnectorInput()
    n.connector = "Acme | gmail | a@b.com | 22222222-2222-2222-2222-222222222222"
    cid, provider = n._resolve_selected_connector()
    assert cid == "22222222-2222-2222-2222-222222222222"
    assert provider == "gmail"


def test_resolve_selected_connector_empty_when_unset():
    n = IDPConnectorInput()
    n.connector = ""
    cid, provider = n._resolve_selected_connector()
    assert cid is None
    assert provider == ""


def test_resolve_selected_connector_provider_from_catalogue_when_hint_absent(monkeypatch):
    """`connector_provider` is a show:false template field, so the run engine may skip it and it
    won't exist on the component. The provider must then still come from the catalogue lookup — so a
    Gmail canvas node routes to the Gmail path even without the provider hint."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "MyGmail"  # note: connector_provider deliberately NOT set on the instance
    monkeypatch.setattr(ci, "_resolve_connector_by_name", lambda name, provider=None: ("id-g", "gmail"))
    cid, provider = n._resolve_selected_connector()
    assert cid == "id-g"
    assert provider == "gmail"


def test_get_document_dispatches_gmail_from_canvas(monkeypatch):
    """A Gmail connector chosen on the canvas → connector_provider="gmail" → the manual pull routes
    to the Gmail path. Previously provider was parsed from an EMPTY `connector` → wrong Outlook path."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "MyGmail"
    n.connector_provider = "gmail"
    monkeypatch.setattr(ci, "_resolve_connector_by_name", lambda name, provider=None: ("id-g", "gmail"))
    called = {}

    def _fake_gmail():
        called["gmail"] = True
        return __import__("agentcore.schema.message", fromlist=["Message"]).Message(text="ok")

    n._get_gmail_document = _fake_gmail
    out = n.get_document()
    assert called.get("gmail") is True
    assert out.text == "ok"


def test_resolve_selected_connector_unresolvable_name_no_id(monkeypatch):
    """An unresolvable plain name (deleted / typo'd connector) must NOT be passed downstream as an
    id — that would reach UUID() and log a noisy error. Return no id so the caller errors cleanly."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "GhostConnector"
    monkeypatch.setattr(ci, "_resolve_connector_by_name", lambda name, provider=None: None)
    cid, _ = n._resolve_selected_connector()
    assert cid is None


def test_resolve_selected_connector_bare_uuid_name(monkeypatch):
    """Defensive: a bare UUID stored directly in connector_name is used as the id when the catalogue
    name lookup misses (so a valid id still works)."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "33333333-3333-3333-3333-333333333333"
    n.connector_provider = "outlook"
    monkeypatch.setattr(ci, "_resolve_connector_by_name", lambda name, provider=None: None)
    cid, provider = n._resolve_selected_connector()
    assert cid == "33333333-3333-3333-3333-333333333333"
    assert provider == "outlook"


def test_resolve_selected_connector_malformed_token_falls_to_name(monkeypatch):
    """A value with '|' whose LAST segment is not a UUID is NOT mis-parsed as a backend token
    (which would wrongly take parts[1] as provider / parts[-1] as id) — it is treated as a name."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = "weird | name | with | pipes"  # 4 parts, but "pipes" isn't a uuid
    seen = {}

    def _fake(name, provider=None):
        seen["name"] = name
        return None

    monkeypatch.setattr(ci, "_resolve_connector_by_name", _fake)
    cid, _ = n._resolve_selected_connector()
    assert seen["name"] == "weird | name | with | pipes"  # whole value used as the name
    assert cid is None


def test_get_config_raises_clean_error_for_unresolvable(monkeypatch):
    """_get_config raises a clear, non-crashing error when the selection doesn't resolve to an id."""
    from agentcore.components.IDP import connector_input as ci

    n = IDPConnectorInput()
    n.connector = ""
    n.connector_name = "GhostConnector"
    monkeypatch.setattr(ci, "_resolve_connector_by_name", lambda name, provider=None: None)
    with pytest.raises(ValueError) as exc:
        n._get_config()
    msg = str(exc.value).lower()
    assert "no longer available" in msg or "no connector selected" in msg


def _mock_db_with_rows(rows):
    """A mock db_service whose with_session().execute().scalars().all() yields `rows`."""
    from unittest.mock import AsyncMock, MagicMock

    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=exec_result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.with_session = MagicMock(return_value=cm)
    return db


def test_resolve_connector_by_name_first_deterministic_on_duplicates():
    """Real `_resolve_connector_by_name` (mocked session): two same-name matches → return the first
    (deterministic order) rather than an insertion-order-dependent pick."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from uuid import UUID
    from agentcore.components.IDP import connector_input as ci

    row1 = SimpleNamespace(id=UUID("44444444-4444-4444-4444-444444444444"), provider="outlook")
    row2 = SimpleNamespace(id=UUID("55555555-5555-5555-5555-555555555555"), provider="outlook")
    db = _mock_db_with_rows([row1, row2])
    with patch("agentcore.services.deps.get_db_service", MagicMock(return_value=db)):
        out = ci._resolve_connector_by_name("Dup", provider="outlook")
    assert out == ("44444444-4444-4444-4444-444444444444", "outlook")


def test_resolve_connector_by_name_none_when_no_rows():
    from unittest.mock import MagicMock, patch
    from agentcore.components.IDP import connector_input as ci

    db = _mock_db_with_rows([])
    with patch("agentcore.services.deps.get_db_service", MagicMock(return_value=db)):
        assert ci._resolve_connector_by_name("Nope") is None


@pytest.mark.anyio
async def test_sync_email_monitors_resolves_canvas_node():
    """The real canvas Connector Input node has type ``ConnectorInput`` + display_name
    "Connector Input" and saves the plain connector NAME in ``connector_name``. sync must match it
    by display_name and resolve the connector by name → a working idp_pipeline EMAIL_MONITOR."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from agentcore.services.trigger.service import TriggerService

    conn_id = uuid4()
    fake_conn = SimpleNamespace(id=conn_id, name="MyOutlook", provider="outlook")

    # session.execute(select(ConnectorCatalogue)) → [fake_conn]; commit is a no-op.
    cc_result = MagicMock()
    cc_result.scalars.return_value.all.return_value = [fake_conn]
    session = MagicMock()
    session.execute = AsyncMock(return_value=cc_result)
    session.commit = AsyncMock()

    flow_data = {
        "nodes": [
            {
                "id": "ConnectorInput-abc",
                "data": {
                    "type": "ConnectorInput",
                    "node": {
                        "display_name": "Connector Input",
                        "template": {
                            "connector_name": {"value": "MyOutlook"},
                            "attachment_filter": {"value": "pdf,png"},
                        },
                    },
                },
            }
        ]
    }

    captured = {}

    async def _fake_create(_session, data):
        captured["cfg"] = data.trigger_config
        return SimpleNamespace(id=uuid4(), trigger_type=data.trigger_type, trigger_config=data.trigger_config)

    svc = TriggerService()
    crud = "agentcore.services.database.models.trigger_config.crud"
    with patch(f"{crud}.get_triggers_by_agent_id", AsyncMock(return_value=[])), \
         patch(f"{crud}.create_trigger_config", _fake_create), \
         patch.object(svc, "register_email_monitor", AsyncMock()):
        await svc.sync_email_monitors_for_agent(
            session=session, agent_id=uuid4(), environment="uat", version="1",
            deployment_id=uuid4(), flow_data=flow_data, created_by=uuid4(),
        )

    cfg = captured.get("cfg")
    assert cfg is not None, "a monitor must be created for the canvas Connector Input node"
    assert cfg["connector_id"] == str(conn_id), "connector resolved from plain name → id"
    assert cfg["ingest_mode"] == "idp_pipeline"
    assert cfg["mail_folder"] == "inbox"
    assert cfg["filter_has_attachments"] is True
    assert cfg["node_id"] == "ConnectorInput-abc"


@pytest.mark.anyio
async def test_sync_email_monitors_skips_non_outlook_and_unknown():
    """A connector_name that doesn't resolve to an Outlook connector → no monitor."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from agentcore.services.trigger.service import TriggerService

    cc_result = MagicMock()
    cc_result.scalars.return_value.all.return_value = []  # no outlook connectors
    session = MagicMock()
    session.execute = AsyncMock(return_value=cc_result)
    session.commit = AsyncMock()

    flow_data = {
        "nodes": [
            {
                "id": "ConnectorInput-x",
                "data": {
                    "type": "ConnectorInput",
                    "node": {
                        "display_name": "Connector Input",
                        "template": {"connector_name": {"value": "Nonexistent"}},
                    },
                },
            }
        ]
    }

    created = {"n": 0}

    async def _fake_create(_session, data):
        created["n"] += 1
        return SimpleNamespace(id=uuid4(), trigger_type=data.trigger_type, trigger_config=data.trigger_config)

    svc = TriggerService()
    crud = "agentcore.services.database.models.trigger_config.crud"
    with patch(f"{crud}.get_triggers_by_agent_id", AsyncMock(return_value=[])), \
         patch(f"{crud}.create_trigger_config", _fake_create), \
         patch.object(svc, "register_email_monitor", AsyncMock()):
        await svc.sync_email_monitors_for_agent(
            session=session, agent_id=uuid4(), environment="uat", version="1",
            deployment_id=uuid4(), flow_data=flow_data, created_by=uuid4(),
        )

    assert created["n"] == 0, "no monitor for an unresolved/non-outlook connector"


@pytest.mark.anyio
async def test_sync_email_monitors_deactivates_orphan():
    """Re-publishing with a connector node whose node_id CHANGED (deleted + re-added) deactivates
    the OLD monitor (orphan), so only one active monitor remains per connector node."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from agentcore.services.trigger.service import TriggerService
    from agentcore.services.database.models.trigger_config.model import TriggerTypeEnum

    conn_id = uuid4()
    fake_conn = SimpleNamespace(id=conn_id, name="MyOutlook", provider="outlook")
    cc_result = MagicMock()
    cc_result.scalars.return_value.all.return_value = [fake_conn]
    session = MagicMock()
    session.execute = AsyncMock(return_value=cc_result)
    session.commit = AsyncMock()
    session.add = MagicMock()

    # An existing ACTIVE email monitor for an OLD node_id (the node the user deleted).
    old_monitor = SimpleNamespace(
        id=uuid4(), trigger_type=TriggerTypeEnum.EMAIL_MONITOR, environment="uat",
        deployment_id=uuid4(), is_active=True,
        trigger_config={"node_id": "OLD-node", "ingest_mode": "idp_pipeline"},
    )
    # New publish: the connector node now has a DIFFERENT node id.
    flow_data = {"nodes": [{"id": "NEW-node", "data": {"type": "ConnectorInput", "node": {
        "display_name": "Connector Input", "template": {"connector_name": {"value": "MyOutlook"}}}}}]}

    created = {}

    async def _fake_create(_s, d):
        created["cfg"] = d.trigger_config
        return SimpleNamespace(id=uuid4(), trigger_type=d.trigger_type, trigger_config=d.trigger_config)

    svc = TriggerService()
    crud = "agentcore.services.database.models.trigger_config.crud"
    with patch(f"{crud}.get_triggers_by_agent_id", AsyncMock(return_value=[old_monitor])), \
         patch(f"{crud}.create_trigger_config", _fake_create), \
         patch.object(svc, "register_email_monitor", AsyncMock()), \
         patch.object(svc, "unregister", AsyncMock()) as unreg:
        await svc.sync_email_monitors_for_agent(
            session=session, agent_id=uuid4(), environment="uat", version="2",
            deployment_id=uuid4(), flow_data=flow_data, created_by=uuid4(),
        )

    assert old_monitor.is_active is False                       # orphan deactivated
    unreg.assert_any_await(old_monitor.id)                      # and its task unregistered
    assert created.get("cfg", {}).get("node_id") == "NEW-node"  # one new monitor for the current node


@pytest.mark.anyio
async def test_sync_email_monitors_deactivates_all_when_node_removed():
    """Publishing with NO connector node (removed entirely) deactivates the agent's email monitors."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from agentcore.services.trigger.service import TriggerService
    from agentcore.services.database.models.trigger_config.model import TriggerTypeEnum

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    old = SimpleNamespace(
        id=uuid4(), trigger_type=TriggerTypeEnum.EMAIL_MONITOR, environment="uat",
        deployment_id=uuid4(), is_active=True, trigger_config={"node_id": "GONE-node"},
    )
    flow_data = {"nodes": []}  # connector node removed

    svc = TriggerService()
    crud = "agentcore.services.database.models.trigger_config.crud"
    with patch(f"{crud}.get_triggers_by_agent_id", AsyncMock(return_value=[old])), \
         patch.object(svc, "unregister", AsyncMock()) as unreg:
        await svc.sync_email_monitors_for_agent(
            session=session, agent_id=uuid4(), environment="uat", version="2",
            deployment_id=uuid4(), flow_data=flow_data, created_by=uuid4(),
        )
    assert old.is_active is False
    unreg.assert_any_await(old.id)


@pytest.mark.anyio
async def test_sync_email_monitors_deactivates_on_provider_change():
    """Re-publishing where the SAME connector node (same node_id) was switched from Outlook to a
    non-Outlook provider deactivates its now-stale email monitor — the node no longer resolves to an
    Outlook email cfg, so it is no longer 'live' — and spawns NO replacement poller."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from agentcore.services.trigger.service import TriggerService
    from agentcore.services.database.models.trigger_config.model import TriggerTypeEnum

    # Catalogue has an Outlook connector, but node N1 now points at a SharePoint one (unresolvable
    # among the Outlook rows → _cfg_from_node returns None).
    cc_result = MagicMock()
    cc_result.scalars.return_value.all.return_value = [
        SimpleNamespace(id=uuid4(), name="MyOutlook", provider="outlook")
    ]
    session = MagicMock()
    session.execute = AsyncMock(return_value=cc_result)
    session.commit = AsyncMock()
    session.add = MagicMock()

    old_monitor = SimpleNamespace(
        id=uuid4(), trigger_type=TriggerTypeEnum.EMAIL_MONITOR, environment="uat",
        deployment_id=uuid4(), is_active=True,
        trigger_config={"node_id": "N1", "ingest_mode": "idp_pipeline"},
    )
    # Same node id N1, but its connector is now SharePoint → not an Outlook email node.
    flow_data = {"nodes": [{"id": "N1", "data": {"type": "ConnectorInput", "node": {
        "display_name": "Connector Input", "template": {"connector_name": {"value": "MySharePoint"}}}}}]}

    created = {"n": 0}

    async def _fake_create(_s, d):
        created["n"] += 1
        return SimpleNamespace(id=uuid4(), trigger_type=d.trigger_type, trigger_config=d.trigger_config)

    svc = TriggerService()
    crud = "agentcore.services.database.models.trigger_config.crud"
    with patch(f"{crud}.get_triggers_by_agent_id", AsyncMock(return_value=[old_monitor])), \
         patch(f"{crud}.create_trigger_config", _fake_create), \
         patch.object(svc, "register_email_monitor", AsyncMock()), \
         patch.object(svc, "unregister", AsyncMock()) as unreg:
        await svc.sync_email_monitors_for_agent(
            session=session, agent_id=uuid4(), environment="uat", version="2",
            deployment_id=uuid4(), flow_data=flow_data, created_by=uuid4(),
        )

    assert old_monitor.is_active is False        # stale Outlook monitor deactivated
    unreg.assert_any_await(old_monitor.id)        # its polling task unregistered
    assert created["n"] == 0                       # the SharePoint node spawns no email poller


def _graph_resp(status, payload):
    from unittest.mock import MagicMock
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=payload)
    return r


def _graph_client(responses):
    """A mock httpx.AsyncClient async-context-manager whose .get() yields `responses` in order."""
    from unittest.mock import AsyncMock, MagicMock
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=responses)
    return client


@pytest.mark.anyio
async def test_fetch_attachments_raw_follows_pagination():
    """Graph paginates the attachments collection via @odata.nextLink. The fetch must follow ALL
    pages — otherwise a single email with many attachments loses everything past the first page.
    Also: item-attachments and attachments without inline contentBytes are skipped."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    page1 = {
        "value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "doc1.pdf",
             "contentBytes": "QUJD", "contentType": "application/pdf", "size": 3},
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "i1", "name": "nested.eml"},  # skipped
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages/m1/attachments?$skiptoken=P2",
    }
    page2 = {
        "value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a2", "name": "doc2.pdf",
             "contentBytes": "REVG", "contentType": "application/pdf", "size": 3},
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "", "name": "noid.pdf",
             "contentBytes": "", "size": 10},  # no inline bytes AND no id → skipped (can't $value-fetch)
        ],
    }
    client = _graph_client([_graph_resp(200, page1), _graph_resp(200, page2)])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out = await svc._fetch_attachments_raw("m1", "tok", "task1")

    assert out is not None
    assert [a["name"] for a in out] == ["doc1.pdf", "doc2.pdf"]  # BOTH pages; item + empty-bytes skipped
    assert client.get.await_count == 2                            # followed nextLink to page 2


@pytest.mark.anyio
async def test_fetch_attachments_raw_retries_on_mid_page_failure():
    """If a LATER page fails, return None so the whole email is retried next poll (dedup skips the
    attachments already ingested) — never mark it seen with a partial attachment set."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    page1 = {
        "value": [{"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "doc1.pdf",
                   "contentBytes": "QUJD", "contentType": "application/pdf", "size": 3}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages/m1/attachments?$skiptoken=P2",
    }
    client = _graph_client([_graph_resp(200, page1), _graph_resp(500, {})])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out = await svc._fetch_attachments_raw("m1", "tok", "task1")

    assert out is None                  # page-2 failure → retry whole email (no partial ingest)
    assert client.get.await_count == 2


@pytest.mark.anyio
async def test_fetch_attachments_raw_returns_none_when_page_cap_exceeded():
    """Hitting the page-cap with pages still remaining is an INCOMPLETE fetch → return None (retry),
    never the partial list (else the caller marks the email seen and permanently drops the rest)."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    # Every page has a nextLink → the cap (patched to 1) is hit with more remaining.
    page = {
        "value": [{"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "d.pdf",
                   "contentBytes": "QUJD", "contentType": "application/pdf", "size": 3}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages/m1/attachments?$skiptoken=NEXT",
    }
    client = _graph_client([_graph_resp(200, page), _graph_resp(200, page)])
    svc = TriggerService()
    with patch("agentcore.services.trigger.service._MAX_ATTACHMENT_PAGES", 1), \
         patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out = await svc._fetch_attachments_raw("m1", "tok", "task1")

    assert out is None                  # cap hit + pages remain → incomplete → retry (no partial)
    assert client.get.await_count == 1  # stopped exactly at the cap


def _value_resp(raw: bytes):
    from unittest.mock import MagicMock
    r = MagicMock()
    r.status_code = 200
    r.content = raw
    return r


@pytest.mark.anyio
async def test_fetch_attachments_raw_fetches_large_via_value():
    """A large attachment (no inline contentBytes from the list endpoint) is fetched via /$value
    instead of being silently dropped (which would also mark the email seen with the doc lost)."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    page = {"value": [
        {"@odata.type": "#microsoft.graph.fileAttachment", "id": "big1", "name": "big.pdf",
         "contentBytes": "", "contentType": "application/pdf", "size": 9_000_000},
    ]}
    client = _graph_client([_graph_resp(200, page), _value_resp(b"RAWPDFBYTES")])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out = await svc._fetch_attachments_raw("m1", "tok", "t")

    import base64
    assert out is not None and len(out) == 1
    assert base64.b64decode(out[0]["content_bytes_b64"]) == b"RAWPDFBYTES"  # large file via $value
    assert client.get.await_count == 2  # list page + $value fetch


@pytest.mark.anyio
async def test_fetch_attachments_raw_rejects_non_graph_nextlink():
    """A nextLink to a non-Graph host (SSRF) is refused — return None, never send the token there."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    page = {
        "value": [{"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "d.pdf",
                   "contentBytes": "QUJD", "contentType": "application/pdf", "size": 3}],
        "@odata.nextLink": "http://169.254.169.254/latest/meta-data/",  # SSRF target
    }
    client = _graph_client([_graph_resp(200, page)])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out = await svc._fetch_attachments_raw("m1", "tok", "t")

    assert out is None                  # bad nextLink refused
    assert client.get.await_count == 1  # only the legit first page was fetched


def test_as_bool_handles_stringified_toggles():
    """bool('false') is True — a stringified toggle must be parsed, not coerced, or a disabled
    'Has Attachments Only' would still filter."""
    from agentcore.services.trigger.service import _as_bool
    assert _as_bool(True) is True and _as_bool(False) is False
    assert _as_bool("false") is False and _as_bool("true") is True
    assert _as_bool("0") is False and _as_bool("1") is True
    assert _as_bool("", True) is False and _as_bool(None, True) is True


def test_is_graph_url_guards_ssrf():
    from agentcore.services.trigger.service import _is_graph_url
    assert _is_graph_url("https://graph.microsoft.com/v1.0/me/messages/x/attachments?$skiptoken=Y")
    assert not _is_graph_url("http://graph.microsoft.com/x")            # not https
    assert not _is_graph_url("https://graph.microsoft.com:8443/x")      # non-443 port
    assert not _is_graph_url("https://169.254.169.254/latest/meta-data/")
    assert not _is_graph_url("https://evil.com/graph.microsoft.com")
    assert not _is_graph_url("")


# ── Manual-pull / preview attachment fetch (get_document) — large files + pagination ──

def _sync_resp(status, payload=None, content=None):
    from unittest.mock import MagicMock
    r = MagicMock()
    r.status_code = status
    if payload is not None:
        r.json = MagicMock(return_value=payload)
    if content is not None:
        r.content = content
    return r


def test_fetch_message_attachments_sync_pagination_and_value():
    """The preview fetch follows @odata.nextLink AND fetches a large attachment (no inline bytes)
    via /$value; item-attachments and no-id/no-bytes items are skipped."""
    from unittest.mock import MagicMock, patch
    from agentcore.components.IDP.connector_input import _fetch_message_file_attachments_sync

    page1 = {
        "value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "s1", "name": "small.pdf",
             "contentBytes": "QUJD"},  # inline → b"ABC"
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "i1", "name": "nested.eml"},  # skip
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages/m1/attachments?$skiptoken=P2",
    }
    page2 = {
        "value": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "big1", "name": "big.pdf",
             "contentBytes": ""},  # large → /$value
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "", "name": "noid.pdf",
             "contentBytes": ""},  # no id + no bytes → skip
        ],
    }
    # list page1 → list page2 → $value(big)
    responses = [_sync_resp(200, payload=page1), _sync_resp(200, payload=page2), _sync_resp(200, content=b"BIGPDF")]
    with patch("httpx.get", MagicMock(side_effect=responses)) as g:
        out = _fetch_message_file_attachments_sync("tok", "m1")

    assert out is not None
    assert [a["name"] for a in out] == ["small.pdf", "big.pdf"]
    assert out[0]["data"] == b"ABC" and out[1]["data"] == b"BIGPDF"  # inline + $value
    assert g.call_count == 3


def test_fetch_message_attachments_sync_ssrf_rejects_nextlink():
    """A non-Graph @odata.nextLink (SSRF) is not followed — keep page-1 results, stop paging."""
    from unittest.mock import MagicMock, patch
    from agentcore.components.IDP.connector_input import _fetch_message_file_attachments_sync

    page = {
        "value": [{"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "d.pdf",
                   "contentBytes": "QUJD"}],
        "@odata.nextLink": "http://169.254.169.254/latest/meta-data/",
    }
    with patch("httpx.get", MagicMock(side_effect=[_sync_resp(200, payload=page)])) as g:
        out = _fetch_message_file_attachments_sync("tok", "m1")

    assert [a["name"] for a in out] == ["d.pdf"]   # page-1 attachment kept
    assert g.call_count == 1                        # did NOT follow the metadata-host nextLink


def test_fetch_message_attachments_sync_initial_failure_returns_none():
    """Initial attachments-list GET failing → None, so get_document falls through to the next email."""
    from unittest.mock import MagicMock, patch
    from agentcore.components.IDP.connector_input import _fetch_message_file_attachments_sync

    with patch("httpx.get", MagicMock(side_effect=[_sync_resp(500)])):
        assert _fetch_message_file_attachments_sync("tok", "m1") is None


def test_fetch_message_attachments_sync_malformed_200_no_crash():
    """A 200 whose body isn't a JSON object degrades to best-effort (None), never crashes preview."""
    from unittest.mock import MagicMock, patch
    from agentcore.components.IDP.connector_input import _fetch_message_file_attachments_sync

    bad = _sync_resp(200, payload=["not", "an", "object"])  # array, not a dict
    with patch("httpx.get", MagicMock(side_effect=[bad])):
        assert _fetch_message_file_attachments_sync("tok", "m1") is None


# ── Message-list pagination (monitor walks @odata.nextLink so high-volume inboxes don't miss mail) ──

@pytest.mark.anyio
async def test_walk_message_pages_follows_nextlink_while_new():
    """Walks to the next page while the latest page still has NEW (unseen) mail; accumulates both."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    first = {"value": [{"id": "m1"}, {"id": "m2"}],
             "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=P2"}
    seen = {"m2": None}  # m1 is new → page further
    client = _graph_client([_graph_resp(200, {"value": [{"id": "m3"}, {"id": "m4"}]})])  # no nextLink → last
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out, ok = await svc._walk_message_pages("tok", first, seen, "t")
    assert ok is True
    assert [m["id"] for m in out] == ["m1", "m2", "m3", "m4"]
    assert client.get.await_count == 1


@pytest.mark.anyio
async def test_walk_message_pages_stops_when_page_all_seen():
    """If the latest page has no new mail, do NOT fetch further pages (don't refetch old mail)."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    first = {"value": [{"id": "m1"}, {"id": "m2"}],
             "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=P2"}
    seen = {"m1": None, "m2": None}  # both seen → no new on page 1
    client = _graph_client([_graph_resp(200, {"value": [{"id": "m3"}]})])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out, ok = await svc._walk_message_pages("tok", first, seen, "t")
    assert ok is True
    assert [m["id"] for m in out] == ["m1", "m2"]
    assert client.get.await_count == 0


@pytest.mark.anyio
async def test_walk_message_pages_rejects_non_graph_nextlink():
    """A non-Graph @odata.nextLink (SSRF) is not followed AND is treated as incomplete (ok=False),
    so the caller won't mark the page-1 messages seen and skip the unreached pages forever."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    first = {"value": [{"id": "m1"}], "@odata.nextLink": "http://169.254.169.254/latest/meta-data/"}
    client = _graph_client([_graph_resp(200, {"value": [{"id": "m2"}]})])
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out, ok = await svc._walk_message_pages("tok", first, {}, "t")
    assert ok is False                       # incomplete → caller must not mark seen
    assert [m["id"] for m in out] == ["m1"]  # page-1 still returned
    assert client.get.await_count == 0       # the metadata-host nextLink was NOT fetched


@pytest.mark.anyio
async def test_walk_message_pages_incomplete_on_cap():
    """Hitting _MAX_MESSAGE_PAGES with pages still remaining is INCOMPLETE → ok=False, so the caller
    won't mark this batch seen and let a later poll's all-seen early-stop hide the overflow pages."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    nl = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=NEXT"
    first = {"value": [{"id": "m1"}], "@odata.nextLink": nl}
    page2 = {"value": [{"id": "m2"}], "@odata.nextLink": nl}  # still more pages after the cap
    client = _graph_client([_graph_resp(200, page2)])
    svc = TriggerService()
    with patch("agentcore.services.trigger.service._MAX_MESSAGE_PAGES", 2), \
         patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out, ok = await svc._walk_message_pages("tok", first, {}, "t")
    assert ok is False                          # cap hit with pages remaining → incomplete
    assert [m["id"] for m in out] == ["m1", "m2"]
    assert client.get.await_count == 1          # fetched page 2, then stopped at the cap


@pytest.mark.anyio
async def test_walk_message_pages_incomplete_on_page_failure():
    """A later-page fetch failure → ok=False so the caller does NOT mark these seen (they'd be
    skipped by the next poll's early-stop). Only the successfully-fetched page-1 is returned."""
    from unittest.mock import MagicMock, patch
    from agentcore.services.trigger.service import TriggerService

    first = {"value": [{"id": "m1"}],
             "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$skiptoken=P2"}
    client = _graph_client([_graph_resp(500, {})])  # page 2 fetch fails
    svc = TriggerService()
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        out, ok = await svc._walk_message_pages("tok", first, {}, "t")
    assert ok is False
    assert [m["id"] for m in out] == ["m1"]
    assert client.get.await_count == 1


def test_get_document_returns_large_attachment():
    """get_document surfaces a big file (previously skipped) and keeps the Message/data shape."""
    import os
    from unittest.mock import MagicMock, patch

    node = _node(connector="", folder="inbox", max_emails=10, fetch_full_body=False)
    node._get_config = MagicMock(return_value={})
    node._get_account = MagicMock(return_value={"email": "x@y.com"})
    node._get_token = MagicMock(return_value="tok")

    msg = {"id": "m1", "subject": "Inv", "from": {"emailAddress": {"address": "basudps@gmail.com"}},
           "hasAttachments": True, "toRecipients": [], "ccRecipients": [], "receivedDateTime": "2026-06-25T00:00:00Z"}
    messages_resp = _sync_resp(200, payload={"value": [msg]})

    cim = "agentcore.components.IDP.connector_input"
    with patch("httpx.get", MagicMock(return_value=messages_resp)), \
         patch(f"{cim}._fetch_message_file_attachments_sync",
               MagicMock(return_value=[{"name": "big.pdf", "data": b"BIGDATA"}])):
        out = node.get_document()

    assert out.data["filename"] == "big.pdf"
    assert out.data["source"] == "mail_connector" and out.data["from"] == "basudps@gmail.com"
    assert os.path.exists(out.data["file_path"])
    with open(out.data["file_path"], "rb") as f:
        assert f.read() == b"BIGDATA"
    os.remove(out.data["file_path"])


def test_g2_connector_ingest_rollback_imports_present():
    """G2 regression guard: the email/SharePoint/OneDrive ingest functions must import the names
    their enqueue-failure rollback uses (`_sql_delete`, `IdpProcessingJob`). Without them the
    rollback raises NameError and the document is left stranded with no job (the bug G2 fixes)."""
    import ast
    import inspect
    from agentcore.services.trigger import service as _svc

    tree = ast.parse(inspect.getsource(_svc))
    for fn in ("_ingest_email_to_idp", "_ingest_sharepoint_files_to_idp", "_ingest_onedrive_files_to_idp"):
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn)
        names = set()
        for n in ast.walk(node):
            if isinstance(n, (ast.ImportFrom, ast.Import)):
                for a in n.names:
                    names.add(a.asname or a.name)
        assert "_sql_delete" in names, f"{fn}: missing `_sql_delete` import (G2 rollback NameError)"
        assert "IdpProcessingJob" in names, f"{fn}: missing `IdpProcessingJob` import (G2 rollback NameError)"
