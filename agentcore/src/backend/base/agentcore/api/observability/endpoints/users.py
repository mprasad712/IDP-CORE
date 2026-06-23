"""GET /users observability endpoint."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from agentcore.services.auth.permissions import normalize_role
from agentcore.services.auth.utils import get_current_active_user
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.user_department_membership.model import UserDepartmentMembership
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.deps import get_session
from agentcore.services.observability.rbac import _user_org_ids, _user_dept_rows

from ..models import UserUsageItem, UserUsageListResponse
from ..parsing import clear_request_caches, compute_date_range
from ..scope import resolve_scope_context, scope_warning_payload
from ..trace_store import TraceStore

router = APIRouter()


@router.get("/users")
async def get_users_usage(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    org_id: Annotated[UUID | None, Query(description="Organization scope")] = None,
    dept_id: Annotated[UUID | None, Query(description="Department scope")] = None,
    from_date: Annotated[str | None, Query(description="Start date (YYYY-MM-DD)")] = None,
    to_date: Annotated[str | None, Query(description="End date (YYYY-MM-DD)")] = None,
    tz_offset: Annotated[int | None, Query(description="Timezone offset in minutes from UTC")] = None,
    environment: Annotated[str | None, Query(description="'uat' or 'production'")] = None,
    trace_scope: Annotated[str, Query(description="Trace scope: 'all', 'dept', or 'my'")] = "all",
) -> UserUsageListResponse:
    """Get usage statistics grouped by user under active organization or department scopes."""
    clear_request_caches()

    role = normalize_role(current_user.role)
    if role not in {"super_admin", "idp_auditor", "doc_approver", "department_admin"}:
        raise HTTPException(
            status_code=403,
            detail="Access denied. Users usage tab is only visible to privileged roles.",
        )

    # 1. Resolve scope context (getting client configurations, warning messages, etc.)
    _, scoped_clients, scope_key, scope_warnings = await resolve_scope_context(
        session=session,
        current_user=current_user,
        org_id=org_id,
        dept_id=dept_id,
        trace_scope=trace_scope,
    )
    if not scoped_clients:
        return UserUsageListResponse(
            users=[],
            total=0,
            **scope_warning_payload(scope_warnings),
        )

    try:
        # 2. Query target users in SQL database based on role constraints
        org_ids = await _user_org_ids(session, current_user.id)
        dept_rows = await _user_dept_rows(session, current_user.id)
        dept_ids = {d for d, _ in dept_rows}

        if role in {"super_admin", "idp_auditor", "doc_approver"}:
            if dept_id:
                # Limit to the selected department
                stmt = (
                    select(User)
                    .join(UserDepartmentMembership, UserDepartmentMembership.user_id == User.id)
                    .where(
                        UserDepartmentMembership.department_id == dept_id,
                        UserDepartmentMembership.status == "active",
                        User.deleted_at.is_(None),
                    )
                )
            else:
                # All users in organization
                stmt = (
                    select(User)
                    .join(UserOrganizationMembership, UserOrganizationMembership.user_id == User.id)
                    .where(
                        UserOrganizationMembership.org_id.in_(list(org_ids)),
                        UserOrganizationMembership.status.in_(["accepted", "active"]),
                        User.deleted_at.is_(None),
                    )
                )
        elif role == "department_admin":
            if dept_id:
                if dept_id not in dept_ids:
                    raise HTTPException(status_code=403, detail="Selected department is outside your scope.")
                target_depts = {dept_id}
            else:
                target_depts = dept_ids
            stmt = (
                select(User)
                .join(UserDepartmentMembership, UserDepartmentMembership.user_id == User.id)
                .where(
                    UserDepartmentMembership.department_id.in_(list(target_depts)),
                    UserDepartmentMembership.status == "active",
                    User.deleted_at.is_(None),
                )
            )
        else:
            stmt = select(User).where(User.id == current_user.id)

        result = await session.exec(stmt)
        db_users = list(result.all())

        # Deduplicate users by ID
        unique_users = {}
        for u in db_users:
            unique_users[str(u.id)] = u

        allowed_uids = set(unique_users.keys())

        if not allowed_uids:
            return UserUsageListResponse(
                users=[],
                total=0,
                **scope_warning_payload(scope_warnings),
            )

        # 3. Retrieve all traces for this set of users in the date range
        from_ts, to_ts = compute_date_range(from_date, to_date, tz_offset, default_days=None)
        
        custom_scope_key = f"{scope_key}:users_tab"
        traces, _ = TraceStore.get_traces(
            clients=scoped_clients,
            allowed_user_ids=allowed_uids,
            scope_key=custom_scope_key,
            from_timestamp=from_ts,
            to_timestamp=to_ts,
            environment=environment,
            fetch_all=True,
        )

        # 4. Aggregate metrics by user
        user_stats = {}
        for uid, u in unique_users.items():
            user_stats[uid] = {
                "user_id": uid,
                "username": u.username,
                "display_name": u.display_name or u.username,
                "email": u.email,
                "role": u.role,
                "trace_count": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "last_activity": None,
            }

        for t in traces:
            if t.user_id and t.user_id in user_stats:
                stats = user_stats[t.user_id]
                stats["trace_count"] += 1
                stats["total_tokens"] += t.total_tokens
                stats["total_cost"] += t.total_cost
                if t.timestamp:
                    if stats["last_activity"] is None or t.timestamp > stats["last_activity"]:
                        stats["last_activity"] = t.timestamp

        # Convert to list and sort by trace_count desc
        users_list = [UserUsageItem(**stats) for stats in user_stats.values()]
        users_list.sort(key=lambda x: x.trace_count, reverse=True)

        return UserUsageListResponse(
            users=users_list,
            total=len(users_list),
            **scope_warning_payload(scope_warnings),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching users usage: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch users usage: {e}")
