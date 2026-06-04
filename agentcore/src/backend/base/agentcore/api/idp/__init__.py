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

router = APIRouter(prefix="/v1/idp", tags=["IDP"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "IDP feature layer"}


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def idp_rbac(
    request: Request,
    current_user: User = Depends(get_current_active_user),
) -> User:
    """B03 — baseline auth + RBAC for every IDP endpoint (method-aware).

    Reads (GET/HEAD/OPTIONS) require ``view_idp``; writes require ``manage_idp``.
    Endpoint-specific permissions are layered by their owners as features are built:
    Processed-Docs review/approve -> ``review_docs``; admin/config overrides -> ``admin_idp``.
    """
    required = "view_idp" if request.method in _READ_METHODS else "manage_idp"
    if permission_cache:
        user_permissions = await permission_cache.get_permissions_for_role(current_user.role)
    else:
        user_permissions = await get_permissions_for_role(current_user.role)
    if required not in user_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required IDP permission: {required}",
        )
    return current_user


_idp_guard = [Depends(idp_rbac)]

router.include_router(field_configs_router, dependencies=_idp_guard)
router.include_router(documents_router, dependencies=_idp_guard)
router.include_router(jobs_router, dependencies=_idp_guard)
router.include_router(processed_docs_router, dependencies=_idp_guard)
router.include_router(batches_router, dependencies=_idp_guard)
router.include_router(rules_router, dependencies=_idp_guard)
router.include_router(idp_agents_router, dependencies=_idp_guard)
