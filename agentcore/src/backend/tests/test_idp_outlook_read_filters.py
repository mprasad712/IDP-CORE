"""Unit tests for the Outlook /read PREVIEW endpoint filter parity with the email monitor.

Pure / fast: no DB, no Graph, no token. Exercises the ReadMailRequest model (new filter fields +
max_results alias) and the two pure helpers that build the OData $filter and do the client-side
fallback match. Mirrors the MONITOR semantics (raw values in OData, lowercased client-side).
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentcore.api import outlook_connector as oc
from agentcore.api.outlook_connector import (
    ReadMailRequest,
    _build_read_odata_filters,
    _read_message_matches,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── ReadMailRequest model ────────────────────────────────────────────

def test_request_accepts_new_filter_fields():
    req = ReadMailRequest(
        account_email="a@b.com",
        filter_body="urgent",
        filter_to="t@y.com",
        filter_cc="c@y.com",
        filter_importance="high",
        filter_has_attachments=True,
        unread_only=True,
    )
    assert req.filter_body == "urgent"
    assert req.filter_to == "t@y.com"
    assert req.filter_cc == "c@y.com"
    assert req.filter_importance == "high"
    assert req.filter_has_attachments is True
    assert req.unread_only is True


def test_request_defaults_are_inert():
    req = ReadMailRequest(account_email="a@b.com")
    assert req.filter_body is None
    assert req.filter_to is None
    assert req.filter_cc is None
    assert req.filter_importance is None
    assert req.filter_has_attachments is False
    assert req.unread_only is False
    assert req.limit == 10


def test_max_results_is_alias_for_limit():
    # The monitor / live tests use `max_results`; it must populate `limit`.
    assert ReadMailRequest(account_email="a@b.com", max_results=3).limit == 3
    # `limit` still works.
    assert ReadMailRequest(account_email="a@b.com", limit=5).limit == 5
    # When both supplied, `limit` (listed first) wins deterministically.
    assert ReadMailRequest(account_email="a@b.com", limit=7, max_results=3).limit == 7


# ── _build_read_odata_filters (mirrors monitor: raw values) ──────────

def test_build_odata_all_filters():
    req = ReadMailRequest(
        account_email="a@b.com",
        filter_sender="x@y.com",
        filter_subject="invoice",
        filter_body="urgent",
        filter_to="t@y.com",
        filter_cc="c@y.com",
        filter_importance="high",
        filter_has_attachments=True,
        unread_only=True,
    )
    joined = " ".join(_build_read_odata_filters(req))
    assert "hasAttachments eq true" in joined
    assert "isRead eq false" in joined
    assert "from/emailAddress/address eq 'x@y.com'" in joined
    assert "contains(subject, 'invoice')" in joined
    assert "contains(body/content, 'urgent')" in joined
    assert "toRecipients/any(r:r/emailAddress/address eq 't@y.com')" in joined
    assert "ccRecipients/any(c:c/emailAddress/address eq 'c@y.com')" in joined
    assert "importance eq 'high'" in joined


def test_build_odata_exact_order_matches_monitor():
    # The clause ORDER must mirror the email monitor exactly (services/trigger/service.py:1581-1596):
    # unread → sender → subject → body → to → cc → importance → has_attachments.
    req = ReadMailRequest(
        account_email="a@b.com",
        filter_sender="x@y.com",
        filter_subject="invoice",
        filter_body="urgent",
        filter_to="t@y.com",
        filter_cc="c@y.com",
        filter_importance="high",
        filter_has_attachments=True,
        unread_only=True,
    )
    assert _build_read_odata_filters(req) == [
        "isRead eq false",
        "from/emailAddress/address eq 'x@y.com'",
        "contains(subject, 'invoice')",
        "contains(body/content, 'urgent')",
        "toRecipients/any(r:r/emailAddress/address eq 't@y.com')",
        "ccRecipients/any(c:c/emailAddress/address eq 'c@y.com')",
        "importance eq 'high'",
        "hasAttachments eq true",
    ]


def test_build_odata_empty_when_no_filters():
    req = ReadMailRequest(account_email="a@b.com")
    assert _build_read_odata_filters(req) == []


def test_build_odata_importance_all_is_skipped():
    req = ReadMailRequest(account_email="a@b.com", filter_importance="all")
    assert _build_read_odata_filters(req) == []


def test_build_odata_has_attachments_false_is_skipped():
    # Mirror monitor: false/absent = no filter (toggle, not tri-state).
    req = ReadMailRequest(account_email="a@b.com", filter_has_attachments=False)
    assert "hasAttachments" not in " ".join(_build_read_odata_filters(req))


def test_build_odata_escapes_single_quotes():
    req = ReadMailRequest(account_email="a@b.com", filter_subject="O'Brien")
    assert "contains(subject, 'O''Brien')" in " ".join(_build_read_odata_filters(req))


@pytest.mark.parametrize(
    "field",
    ["filter_sender", "filter_subject", "filter_body", "filter_to", "filter_cc", "filter_importance"],
)
def test_build_odata_escapes_all_interpolated_fields(field):
    # Every user-supplied value interpolated into an OData clause must have its single quotes doubled
    # (injection safety). A bare "'" would otherwise terminate the literal.
    req = ReadMailRequest(account_email="a@b.com", **{field: "x'y"})
    joined = " ".join(_build_read_odata_filters(req))
    assert "x''y" in joined
    assert "x'y'" not in joined.replace("x''y", "")  # no un-doubled quote left over


def test_build_odata_empty_string_filters_are_skipped():
    req = ReadMailRequest(
        account_email="a@b.com",
        filter_sender="", filter_subject="", filter_body="",
        filter_to="", filter_cc="", filter_importance="",
    )
    assert _build_read_odata_filters(req) == []


# ── _read_message_matches (client-side fallback, lowercased) ─────────

def _msg(**kw):
    base = {
        "from": {"emailAddress": {"address": "Sender@Example.com"}},
        "subject": "Monthly Invoice",
        "body": {"content": "Please pay URGENT"},
        "bodyPreview": "Please pay URGENT",
        "toRecipients": [{"emailAddress": {"address": "To@Example.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "Cc@Example.com"}}],
        "importance": "high",
        "isRead": False,
        "hasAttachments": True,
    }
    base.update(kw)
    return base


def test_match_no_filters_passes():
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com")) is True


def test_match_sender_case_insensitive_exact():
    req = ReadMailRequest(account_email="a@b.com", filter_sender="sender@example.com")
    assert _read_message_matches(_msg(), req) is True
    req2 = ReadMailRequest(account_email="a@b.com", filter_sender="other@example.com")
    assert _read_message_matches(_msg(), req2) is False


def test_match_subject_substring_case_insensitive():
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_subject="invoice")) is True
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_subject="receipt")) is False


def test_match_body_substring_case_insensitive():
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_body="urgent")) is True
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_body="refund")) is False


def test_match_body_falls_back_to_preview_when_no_body():
    m = _msg(body={})
    assert _read_message_matches(m, ReadMailRequest(account_email="a@b.com", filter_body="urgent")) is True


def test_match_to_and_cc_exact_address_case_insensitive():
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_to="to@example.com")) is True
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_to="nope@example.com")) is False
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_cc="cc@example.com")) is True
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_cc="nope@example.com")) is False


def test_match_importance_exact_all_means_any():
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_importance="high")) is True
    assert _read_message_matches(_msg(), ReadMailRequest(account_email="a@b.com", filter_importance="low")) is False
    # "all" / None → no importance filtering
    assert _read_message_matches(_msg(importance="low"), ReadMailRequest(account_email="a@b.com", filter_importance="all")) is True
    assert _read_message_matches(_msg(importance="low"), ReadMailRequest(account_email="a@b.com")) is True


def test_match_has_attachments_toggle():
    # True requires attachments; a msg without attachments is excluded.
    assert _read_message_matches(_msg(hasAttachments=False), ReadMailRequest(account_email="a@b.com", filter_has_attachments=True)) is False
    assert _read_message_matches(_msg(hasAttachments=True), ReadMailRequest(account_email="a@b.com", filter_has_attachments=True)) is True
    # False = no filter → a msg without attachments still passes.
    assert _read_message_matches(_msg(hasAttachments=False), ReadMailRequest(account_email="a@b.com", filter_has_attachments=False)) is True


def test_match_unread_only_toggle():
    assert _read_message_matches(_msg(isRead=True), ReadMailRequest(account_email="a@b.com", unread_only=True)) is False
    assert _read_message_matches(_msg(isRead=False), ReadMailRequest(account_email="a@b.com", unread_only=True)) is True
    # unread_only False → read messages still pass.
    assert _read_message_matches(_msg(isRead=True), ReadMailRequest(account_email="a@b.com", unread_only=False)) is True


def test_match_combines_filters_and_logic():
    req = ReadMailRequest(
        account_email="a@b.com",
        filter_subject="invoice",
        filter_importance="high",
        unread_only=True,
    )
    assert _read_message_matches(_msg(), req) is True
    # one criterion fails → whole match fails (AND logic)
    assert _read_message_matches(_msg(isRead=True), req) is False


def test_match_is_none_safe_on_sparse_message():
    # Graph may omit fields entirely; the matcher must not crash on a near-empty message.
    assert _read_message_matches({}, ReadMailRequest(account_email="a@b.com")) is True
    assert _read_message_matches({}, ReadMailRequest(account_email="a@b.com", filter_sender="x@y.com")) is False
    assert _read_message_matches(
        {"from": None, "toRecipients": None, "subject": None, "body": None},
        ReadMailRequest(account_email="a@b.com", filter_to="t@y.com"),
    ) is False


# ── Endpoint-level: read_mail() glue (mocked connector + Graph) ──────

class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _FakeClientFactory:
    """Stands in for httpx.AsyncClient: each `async with ... as c` shares one factory's state so we
    can assert the params of each GET and return a scripted sequence of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "params": params})
        return self.responses.pop(0)


def _patch_connector(monkeypatch, factory):
    """Patch read_mail's dependencies so no DB/token/Graph is touched — only the factory runs."""
    async def _fake_load_connector(connector_id, current_user, session):
        return SimpleNamespace(id=connector_id, provider="outlook", provider_config={})

    monkeypatch.setattr(oc, "_load_connector", _fake_load_connector)
    monkeypatch.setattr(oc, "_get_decrypted_config", lambda row: {"linked_accounts": [{"email": "a@b.com"}]})

    async def _fake_refresh(config, acct, force=False):
        return "tok", False

    monkeypatch.setattr(oc, "_refresh_token_if_needed", _fake_refresh)
    monkeypatch.setattr(oc.httpx, "AsyncClient", factory)


def _graph_msg(importance="high", is_read=False, has_att=True):
    return {
        "id": f"id-{importance}-{is_read}",
        "subject": "Monthly Invoice",
        "from": {"emailAddress": {"address": "sender@example.com"}},
        "receivedDateTime": "2026-06-29T00:00:00Z",
        "bodyPreview": "pay urgent",
        "body": {"content": "pay urgent", "contentType": "text"},
        "hasAttachments": has_att,
        "importance": importance,
        "isRead": is_read,
        "toRecipients": [{"emailAddress": {"address": "to@example.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "cc@example.com"}}],
    }


@pytest.mark.anyio
async def test_read_mail_odata_path_builds_filter_and_returns_new_fields(monkeypatch):
    factory = _FakeClientFactory([_FakeResp(200, {"value": [_graph_msg(importance="high")]})])
    _patch_connector(monkeypatch, factory)

    req = ReadMailRequest(account_email="a@b.com", filter_importance="high", filter_to="to@example.com", max_results=4)
    result = await oc.read_mail(uuid4(), req, SimpleNamespace(id=uuid4()), None)

    sent = factory.calls[0]["params"]
    # max_results alias flowed through to $top; new filters appear in the OData $filter.
    assert sent["$top"] == "4"
    assert "importance eq 'high'" in sent["$filter"]
    assert "toRecipients/any(r:r/emailAddress/address eq 'to@example.com')" in sent["$filter"]
    assert "importance" in sent["$select"] and "isRead" in sent["$select"]
    # Response now surfaces importance + isRead.
    assert result["count"] == 1
    assert result["messages"][0]["importance"] == "high"
    assert result["messages"][0]["isRead"] is False


# ── Hardening fixes (#1 null-safe formatter, #2 limit bounds, #3 importance case, #4 fallback $top)

@pytest.mark.parametrize("bad", [0, -1, 51, 1000])
def test_limit_out_of_bounds_rejected(bad):
    # #2: limit must be 1..50; nonsense values are refused cleanly (422) not sent to Graph as $top.
    with pytest.raises(ValidationError):
        ReadMailRequest(account_email="a@b.com", limit=bad)


def test_limit_bounds_accept_edges():
    assert ReadMailRequest(account_email="a@b.com", limit=1).limit == 1
    assert ReadMailRequest(account_email="a@b.com", limit=50).limit == 50


def test_max_results_alias_is_also_bounded():
    # The alias must enforce the same bounds as limit.
    with pytest.raises(ValidationError):
        ReadMailRequest(account_email="a@b.com", max_results=0)
    with pytest.raises(ValidationError):
        ReadMailRequest(account_email="a@b.com", max_results=999)


def test_build_odata_importance_uppercase_all_is_skipped():
    # #3: "ALL"/"All" (any case) means "no filter", just like lowercase "all".
    assert _build_read_odata_filters(ReadMailRequest(account_email="a@b.com", filter_importance="ALL")) == []
    assert _build_read_odata_filters(ReadMailRequest(account_email="a@b.com", filter_importance="All")) == []


def test_build_odata_importance_is_normalized_lowercase():
    # #3: Graph's importance enum is lowercase; normalize before interpolating + strip whitespace.
    assert "importance eq 'high'" in " ".join(
        _build_read_odata_filters(ReadMailRequest(account_email="a@b.com", filter_importance="High")))
    assert "importance eq 'high'" in " ".join(
        _build_read_odata_filters(ReadMailRequest(account_email="a@b.com", filter_importance=" high ")))


def test_match_importance_case_insensitive_and_uppercase_all():
    # #3: matcher honours the same normalization.
    assert _read_message_matches(_msg(importance="normal"),
                                 ReadMailRequest(account_email="a@b.com", filter_importance="ALL")) is True
    assert _read_message_matches(_msg(importance="high"),
                                 ReadMailRequest(account_email="a@b.com", filter_importance="HIGH")) is True
    assert _read_message_matches(_msg(importance="normal"),
                                 ReadMailRequest(account_email="a@b.com", filter_importance="High")) is False


@pytest.mark.anyio
async def test_read_mail_fallback_filters_client_side(monkeypatch):
    # OData $filter rejected (400) → refetch without $filter → client-side matcher drops the non-match.
    factory = _FakeClientFactory([
        _FakeResp(400, {}),
        _FakeResp(200, {"value": [_graph_msg(importance="high"), _graph_msg(importance="low")]}),
    ])
    _patch_connector(monkeypatch, factory)

    req = ReadMailRequest(account_email="a@b.com", filter_importance="high", limit=10)
    result = await oc.read_mail(uuid4(), req, SimpleNamespace(id=uuid4()), None)

    # Second (fallback) call dropped $filter and widened $top.
    fallback = factory.calls[1]["params"]
    assert "$filter" not in fallback
    assert fallback["$top"] == "50"  # min(limit*5, 50)
    # Only the high-importance message survives the client-side filter.
    assert result["count"] == 1
    assert result["messages"][0]["importance"] == "high"


@pytest.mark.anyio
async def test_read_mail_formatter_is_null_safe(monkeypatch):
    # #1: Graph can return explicit null for from/body/recipients (e.g. some drafts/system msgs).
    # The response formatter must not crash; it should emit sensible defaults.
    null_msg = {
        "id": "x", "subject": None, "from": None, "body": None,
        "toRecipients": None, "ccRecipients": None,
        "receivedDateTime": None, "bodyPreview": None,
    }
    factory = _FakeClientFactory([_FakeResp(200, {"value": [null_msg]})])
    _patch_connector(monkeypatch, factory)

    req = ReadMailRequest(account_email="a@b.com", limit=5)
    result = await oc.read_mail(uuid4(), req, SimpleNamespace(id=uuid4()), None)

    assert result["count"] == 1
    m = result["messages"][0]
    assert m["from"] == {}
    assert m["body"] == ""
    assert m["toRecipients"] == []
    assert m["ccRecipients"] == []
    assert m["importance"] == "normal"
    assert m["isRead"] is False


@pytest.mark.anyio
async def test_read_mail_fallback_top_matches_monitor_fixed_50(monkeypatch):
    # #4: the fallback fetch width must be a fixed 50 (mirrors the monitor), not min(limit*5,50),
    # so a small limit doesn't under-fetch candidates before client-side filtering.
    factory = _FakeClientFactory([_FakeResp(400, {}), _FakeResp(200, {"value": []})])
    _patch_connector(monkeypatch, factory)

    req = ReadMailRequest(account_email="a@b.com", filter_subject="x", limit=2)
    await oc.read_mail(uuid4(), req, SimpleNamespace(id=uuid4()), None)

    assert factory.calls[1]["params"]["$top"] == "50"


@pytest.mark.anyio
async def test_read_mail_importance_normalized_through_route(monkeypatch):
    # End-to-end: uppercase/padded importance flows through read_mail to the OData $filter normalized.
    factory = _FakeClientFactory([_FakeResp(200, {"value": [_graph_msg(importance="high")]})])
    _patch_connector(monkeypatch, factory)
    await oc.read_mail(uuid4(), ReadMailRequest(account_email="a@b.com", filter_importance="HIGH"),
                       SimpleNamespace(id=uuid4()), None)
    assert "importance eq 'high'" in factory.calls[0]["params"]["$filter"]

    # " ALL " (any case, padded) → no importance clause at all (no $filter built).
    factory2 = _FakeClientFactory([_FakeResp(200, {"value": [_graph_msg()]})])
    _patch_connector(monkeypatch, factory2)
    await oc.read_mail(uuid4(), ReadMailRequest(account_email="a@b.com", filter_importance=" ALL "),
                       SimpleNamespace(id=uuid4()), None)
    assert "$filter" not in factory2.calls[0]["params"]


@pytest.mark.anyio
async def test_read_mail_formatter_handles_null_recipient_element(monkeypatch):
    # A null ELEMENT inside the recipients array (not just a null array) must not crash the formatter.
    msg = _graph_msg()
    msg["toRecipients"] = [None, {"emailAddress": {"address": "real@example.com"}}]
    factory = _FakeClientFactory([_FakeResp(200, {"value": [msg]})])
    _patch_connector(monkeypatch, factory)
    result = await oc.read_mail(uuid4(), ReadMailRequest(account_email="a@b.com", limit=5),
                                SimpleNamespace(id=uuid4()), None)
    tos = result["messages"][0]["toRecipients"]
    assert tos[0] == {}                                  # null element → empty dict, no crash
    assert tos[1] == {"address": "real@example.com"}
