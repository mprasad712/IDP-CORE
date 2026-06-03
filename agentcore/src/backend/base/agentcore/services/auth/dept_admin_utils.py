"""Centralised dept-admin lookup utilities.

Single source of truth for "who admins a department?" — backed by the
UserDepartmentMembership table (role_id = department_admin role) rather than
the legacy Department.admin_user_id field.

Migration note: admin_user_id is kept as a nullable convenience field for
the founding admin audit trail only.  All permission / routing / notification
logic must go through these helpers so that multiple co-admins and admin
replacements work transparently.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agentcore.services.database.models.department.model import Department
from agentcore.services.database.models.role.model import Role
from agentcore.services.database.models.user_department_membership.model import (
    UserDepartmentMembership,
)


async def get_dept_admin_ids(
    db: AsyncSession,
    dept_id: UUID,
) -> list[UUID]:
    """Return all active dept-admin user IDs for a department.

    Queries UserDepartmentMembership with role=department_admin.
    Falls back to Department.admin_user_id if the membership table has no
    department_admin rows yet (backward-compat for un-migrated depts).
    """
    rows = (
        await db.exec(
            select(UserDepartmentMembership.user_id)
            .join(Role, Role.id == UserDepartmentMembership.role_id)
            .where(
                UserDepartmentMembership.department_id == dept_id,
                UserDepartmentMembership.status == "active",
                func.lower(Role.name) == "department_admin",
            )
            .order_by(UserDepartmentMembership.assigned_at.asc())
        )
    ).all()

    if rows:
        return [r if isinstance(r, UUID) else r[0] for r in rows]

    # Fallback: legacy single-admin field
    dept = await db.get(Department, dept_id)
    if dept and dept.admin_user_id:
        return [dept.admin_user_id]
    return []


async def get_primary_dept_admin_id(
    db: AsyncSession,
    dept_id: UUID,
) -> UUID | None:
    """Return the primary (oldest-assigned) dept-admin user ID.

    Used wherever a single user_id is required — e.g. request_to FK in approval
    records, legacy notification recipient.  Co-admins are notified separately
    via get_dept_admin_ids().
    """
    ids = await get_dept_admin_ids(db, dept_id)
    return ids[0] if ids else None


async def get_managed_dept_ids_for_user(
    db: AsyncSession,
    user_id: UUID,
) -> set[UUID]:
    """Return dept IDs where user_id is a dept admin.

    Checks membership table (role=department_admin) and falls back to
    Department.admin_user_id for backward-compat.
    """
    rows = (
        await db.exec(
            select(UserDepartmentMembership.department_id)
            .join(Role, Role.id == UserDepartmentMembership.role_id)
            .where(
                UserDepartmentMembership.user_id == user_id,
                UserDepartmentMembership.status == "active",
                func.lower(Role.name) == "department_admin",
            )
        )
    ).all()
    dept_ids: set[UUID] = {r if isinstance(r, UUID) else r[0] for r in rows}

    # Fallback: depts where user is the legacy primary admin
    legacy = (
        await db.exec(select(Department.id).where(Department.admin_user_id == user_id))
    ).all()
    dept_ids.update(legacy)

    return dept_ids


async def is_dept_admin(
    db: AsyncSession,
    user_id: UUID,
    dept_id: UUID,
) -> bool:
    """Return True if user_id is an active dept admin of dept_id."""
    managed = await get_managed_dept_ids_for_user(db, user_id)
    return dept_id in managed
