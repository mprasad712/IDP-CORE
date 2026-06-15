"""Tests for the IDP authorization layer.

Covers the method-aware ``idp_rbac`` router guard (reads require ``view_idp``,
writes require ``manage_idp``) and confirms the seeded IDP permissions resolve
correctly for the non-root roles they were granted to.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import agentcore.api.idp as idp_module
from agentcore.api.idp import idp_rbac


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _stub_permissions(monkeypatch, perms):
    """Force the guard down its non-cached path with a fixed permission set.

    Avoids the async Redis cache so the guard logic can be tested directly.
    """
    monkeypatch.setattr(idp_module, "permission_cache", None)

    async def _fake(role):
        return perms

    monkeypatch.setattr(idp_module, "get_permissions_for_role", _fake)


@pytest.mark.anyio
async def test_read_requires_view_idp(monkeypatch):
    _stub_permissions(monkeypatch, ["view_idp"])
    user = SimpleNamespace(role="idp_configurator")

    # GET is a read -> allowed with view_idp
    assert await idp_rbac(SimpleNamespace(method="GET"), user) is user
    assert await idp_rbac(SimpleNamespace(method="HEAD"), user) is user

    # POST is a write -> needs manage_idp, which this user lacks -> 403
    with pytest.raises(HTTPException) as exc:
        await idp_rbac(SimpleNamespace(method="POST"), user)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_write_requires_manage_idp(monkeypatch):
    _stub_permissions(monkeypatch, ["view_idp", "manage_idp"])
    user = SimpleNamespace(role="super_admin")

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert await idp_rbac(SimpleNamespace(method=method), user) is user
    # Reads still work too
    assert await idp_rbac(SimpleNamespace(method="GET"), user) is user


@pytest.mark.anyio
async def test_no_idp_permission_is_denied(monkeypatch):
    _stub_permissions(monkeypatch, [])
    user = SimpleNamespace(role="doc_submitter")

    with pytest.raises(HTTPException) as exc:
        await idp_rbac(SimpleNamespace(method="GET"), user)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_seeded_permissions_resolve_for_roles():
    """The seed migration's grants are readable through the DB resolver."""
    from agentcore.services.auth.permissions import _get_permissions_for_role_db

    super_admin = set(await _get_permissions_for_role_db("super_admin"))
    assert {"view_idp", "manage_idp", "review_docs", "admin_idp"} <= super_admin

    developer = set(await _get_permissions_for_role_db("idp_configurator"))
    assert {"view_idp", "manage_idp", "review_docs"} <= developer
    assert "admin_idp" not in developer
