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
