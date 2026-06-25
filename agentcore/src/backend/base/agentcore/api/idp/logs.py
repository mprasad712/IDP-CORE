"""Document Logs — returns ALL documents regardless of agent pipeline config."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import apaginate
from pydantic import BaseModel
from sqlmodel import select
import warnings

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.auth.permissions import normalize_role
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.idp.config import IdpAgent
from agentcore.services.database.models.idp.documents import IdpDocument
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership

router = APIRouter(prefix="/logs", tags=["IDP Logs"])


class DocumentLogRead(BaseModel):
    id: UUID
    original_filename: str
    status: str
    created_at: datetime
    agent_id: UUID
    agent_name: str | None = None

    class Config:
        from_attributes = True


async def _get_org_ids(session: DbSession, user_id: UUID) -> set[UUID]:
    rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.status.in_(["accepted", "active"]),
            )
        )
    ).all()
    return {r if isinstance(r, UUID) else r[0] for r in rows}


@router.get("/", response_model=Page[DocumentLogRead])
async def list_document_logs(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    params: Params,
    status_filter: str | None = None,
):
    """List ALL documents with their current status — no pipeline-config gating."""
    role = normalize_role(getattr(current_user, "role", None))

    stmt = (
        select(IdpDocument, IdpAgent, Agent)
        .join(IdpAgent, IdpDocument.agent_id == IdpAgent.id)
        .join(Agent, IdpAgent.agent_id == Agent.id)
    )

    if role != "root":
        org_ids = await _get_org_ids(session, current_user.id)
        stmt = stmt.where(Agent.org_id.in_(list(org_ids)))

    if status_filter:
        stmt = stmt.where(IdpDocument.status == status_filter)

    stmt = stmt.order_by(IdpDocument.created_at.desc())

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=DeprecationWarning,
            module=r"fastapi_pagination\.ext\.sqlalchemy",
        )
        raw_page = await apaginate(session, stmt, params=params)

    items: list[DocumentLogRead] = []
    for row in raw_page.items:
        doc, _idp_agent, agent = row
        items.append(
            DocumentLogRead(
                id=doc.id,
                original_filename=doc.original_filename,
                status=doc.status,
                created_at=doc.created_at,
                agent_id=doc.agent_id,
                agent_name=agent.name if agent else None,
            )
        )

    return raw_page.model_copy(update={"items": items})
