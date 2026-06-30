"""Unit tests for environment-scoped email dedup keys.

The email monitor dedups attachments by (agent_id, dedup_key). To let a UAT version and a PROD
version each ingest the SAME mailbox independently (Basu's requirement), the dedup key is prefixed
with the environment — so the same email yields a different key per environment (both ingest), while
a re-poll within the same environment yields the same key (skipped).
"""
from agentcore.services.trigger.service import _email_dedup_key


def test_key_includes_environment_prefix():
    assert _email_dedup_key("uat", "msg1", "att1") == "uat:msg1:att1"
    assert _email_dedup_key("prod", "msg1", "att1") == "prod:msg1:att1"


def test_uat_and_prod_keys_differ_for_same_email():
    # Same email+attachment, different env → different key → both versions ingest independently.
    assert _email_dedup_key("uat", "m", "a") != _email_dedup_key("prod", "m", "a")


def test_same_env_same_email_is_stable():
    # Same env re-poll → identical key → deduped (skipped).
    assert _email_dedup_key("uat", "m", "a") == _email_dedup_key("uat", "m", "a")


def test_environment_is_case_insensitive():
    assert _email_dedup_key("UAT", "m", "a") == _email_dedup_key("uat", "m", "a")
    assert _email_dedup_key(" Prod ", "m", "a") == _email_dedup_key("prod", "m", "a")


def test_missing_environment_is_safe():
    assert _email_dedup_key(None, "m", "a") == ":m:a"
    assert _email_dedup_key("", "m", "a") == ":m:a"
