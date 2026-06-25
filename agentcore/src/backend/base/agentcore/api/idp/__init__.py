from fastapi import APIRouter, Depends, HTTPException, Request, status

from agentcore.services.auth.utils import get_current_active_user
from agentcore.services.auth.permissions import get_permissions_for_role, permission_cache
from agentcore.services.database.models.user.model import User
from agentcore.api.idp.field_configs import router as field_configs_router
from agentcore.api.idp.documents import router as documents_router
from agentcore.api.idp.jobs import router as jobs_router
from agentcore.api.idp.processed_docs import router as processed_docs_router
from agentcore.api.idp.batches import router as batches_router
from agentcore.api.idp.rules import router as rules_router
from agentcore.api.idp.idp_agents import router as idp_agents_router
from agentcore.api.idp.reports import router as reports_router
from agentcore.api.idp.matching import router as matching_router
from agentcore.api.idp.logs import router as logs_router

router = APIRouter(prefix="/v1/idp", tags=["IDP"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "IDP feature layer"}


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def _get_user_permissions(role: str) -> list[str]:
    if permission_cache:
        return await permission_cache.get_permissions_for_role(role)
    return await get_permissions_for_role(role)


def _make_rbac(read_perm: str, write_perm: str):
    """Factory for method-aware IDP dependency functions."""

    async def _rbac(
        request: Request,
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        required = read_perm if request.method in _READ_METHODS else write_perm
        user_permissions = await _get_user_permissions(current_user.role)
        if required not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required IDP permission: {required}",
            )
        return current_user

    return _rbac


# Baseline guard: read=view_idp, write=manage_idp (field configs, rules, agents, jobs).
idp_rbac = _make_rbac("view_idp", "manage_idp")

# Document submission: read=view_idp, write=submit_documents.
_idp_submit_rbac = _make_rbac("view_idp", "submit_documents")

# HITL review: read=view_idp, write=review_docs.
_idp_review_rbac = _make_rbac("view_idp", "review_docs")


async def idp_approve_rbac(
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Final-approval endpoints always require approve_documents regardless of HTTP method."""
    user_permissions = await _get_user_permissions(current_user.role)
    if "approve_documents" not in user_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing required IDP permission: approve_documents",
        )
    return current_user


_idp_guard = [Depends(idp_rbac)]
_idp_submit_guard = [Depends(_idp_submit_rbac)]
_idp_review_guard = [Depends(_idp_review_rbac)]

router.include_router(field_configs_router, dependencies=_idp_guard)
router.include_router(documents_router, dependencies=_idp_submit_guard)
router.include_router(jobs_router, dependencies=_idp_guard)
router.include_router(processed_docs_router, dependencies=_idp_review_guard)
router.include_router(batches_router, dependencies=_idp_submit_guard)
router.include_router(rules_router, dependencies=_idp_guard)
router.include_router(idp_agents_router, dependencies=_idp_guard)
# Reports are read-only GETs → baseline guard (read=view_idp).
router.include_router(reports_router, dependencies=_idp_guard)
# SAP matching — read=view_idp, write=review_docs (reviewer triggers + accepts discrepancies).
router.include_router(matching_router, dependencies=_idp_review_guard)
router.include_router(logs_router, dependencies=_idp_guard)
