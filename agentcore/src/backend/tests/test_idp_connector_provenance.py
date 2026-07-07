"""Unit tests for email-connector document provenance.

The IDP email monitor records WHERE an ingested attachment came from (connector, mailbox, folder)
and WHICH filters selected it, into each document's ``source_metadata`` — for rich, auditable
logging. ``build_connector_provenance`` is the pure sanitizer that builds that provenance dict.

Critical safety property: a decrypted connector token/secret can NEVER reach ``source_metadata``.
The helper drops any secret-looking key (token/secret/password/api_key/...) regardless of value,
so even a mis-passed ``access_token`` filter is stripped. These tests are pure/fast (no DB, no
Graph, no token).
"""
from agentcore.services.trigger.service import build_connector_provenance


def _flatten_values(d):
    """Yield every scalar value nested anywhere in a dict, for a leak scan."""
    for v in d.values():
        if isinstance(v, dict):
            yield from _flatten_values(v)
        else:
            yield v


def test_build_connector_provenance_sanitizes():
    prov = build_connector_provenance(
        "My Outlook",
        "inv@x.com",
        "Inbox",
        {
            "sender": "v@x.com",
            "subject": "invoice",
            "has_attachments": True,
            "unread_only": False,
            "filter_body": "",
            "access_token": "SECRET",
        },
    )

    # Provenance location keys present.
    assert prov["connector_name"] == "My Outlook"
    assert prov["mailbox_email"] == "inv@x.com"
    assert prov["mail_folder"] == "Inbox"

    filters = prov["filters_applied"]
    # Active filters kept.
    assert filters["sender"] == "v@x.com"
    assert filters["subject"] == "invoice"
    assert filters["has_attachments"] is True
    # A boolean filter that is False is dropped (not an "applied" filter).
    assert "unread_only" not in filters
    # An empty-string filter is dropped.
    assert "filter_body" not in filters

    # SECRET LEAK GUARD: the token key must not appear ANYWHERE, and its value must not leak.
    assert "access_token" not in filters
    assert "access_token" not in prov
    assert "SECRET" not in list(_flatten_values(prov))


def test_build_connector_provenance_empty():
    # All-None / empty args → clean dict with no None values (may be empty).
    prov = build_connector_provenance(None, None, None, None)
    assert isinstance(prov, dict)
    assert None not in prov.values()
    assert "" not in prov.values()
    # No filters passed → no filters_applied key.
    assert "filters_applied" not in prov

    # A single present arg → only that key.
    prov2 = build_connector_provenance("Only Name", None, "", {})
    assert prov2 == {"connector_name": "Only Name"}


def test_build_connector_provenance_redacts_all_secret_markers():
    # Every secret marker (case-insensitive, substring match) must be stripped.
    prov = build_connector_provenance(
        "C",
        "m@x.com",
        "inbox",
        {
            "Access_Token": "a",
            "REFRESH_TOKEN": "b",
            "client_secret": "c",
            "api_key": "d",
            "Password": "e",
            "some_secret": "f",
            "bearer_token": "g",
            "sender": "keep@x.com",
        },
    )
    filters = prov["filters_applied"]
    assert filters == {"sender": "keep@x.com"}
    # Absolutely no secret value survived anywhere.
    for leaked in ("a", "b", "c", "d", "e", "f", "g"):
        assert leaked not in list(_flatten_values(prov))
