"""Canonical IDP organization-scope resolution.

Single source of truth for "which orgs may this user see" used by the IDP read paths
(dashboard metrics, document log download). ``root`` sees everything; every other role is
restricted to the orgs they are an active/accepted member of. Lives in ``services/`` so
both ``services/idp/*`` and ``api/idp/*`` can import it without a circular dependency.
"""
from __future__ import annotations

from uuid import UUID

from sqlmodel import select

from agentcore.services.auth.permissions import normalize_role
from agentcore.services.database.models.user_organization_membership.model import (
    UserOrganizationMembership,
)


async def resolve_org_scope(session, current_user) -> tuple[bool, list[UUID]]:
    """Return ``(is_root, org_ids)``.

    ``root`` → ``(True, [])`` (callers skip scoping). Any other role → ``(False, [...])`` of
    the orgs the user actively belongs to. A non-root user with no orgs yields ``[]``, which
    makes ``IN ([])`` filters match nothing (counts come back 0) — safe, no cross-tenant leak.
    """
    # Use the canonical role normalizer (strips whitespace, normalizes separators, applies
    # aliases) so root-detection matches the rest of the app — e.g. get_document_file.
    role = normalize_role(getattr(current_user, "role", "") or "")
    if role == "root":
        return True, []
    rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == current_user.id,
                UserOrganizationMembership.status.in_(["accepted", "active"]),
            )
        )
    ).all()
    org_ids = [r if isinstance(r, UUID) else r[0] for r in rows]
    return False, org_ids
