from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import apaginate
from sqlmodel import select, or_
from pydantic import BaseModel, Field as PydanticField, model_validator

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.auth.permissions import normalize_role

router = APIRouter(prefix="/idp-agents", tags=["IDP Agents"])

# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

class IdpAgentCreate(BaseModel):
    agent_id: UUID = PydanticField(..., description="UUID of the base Agent to link")
    extraction_mode: str = PydanticField(..., description="Mode: dynamic_prompting, named_config, or multimodal")
    field_config_id: UUID | None = PydanticField(default=None, description="Linked field configuration UUID")
    dynamic_prompt: str | None = PydanticField(default=None, description="Dynamic prompting text")
    preprocessing_steps: dict | None = PydanticField(default=None, description="Preprocessing steps flags")
    multi_doc_split: bool = PydanticField(default=False, description="Enable multi-document splitting")
    default_rule_action: str = PydanticField(default="pending_review", description="Default rule action: auto_approve or pending_review")
    auto_stop_after_batch: bool = PydanticField(default=True, description="Stop after batch processing complete")
    is_active: bool = PydanticField(default=True, description="Is the IDP config active")
    extra: dict | None = PydanticField(default=None, description="JSON escape hatch")

    @model_validator(mode="after")
    def validate_mode_and_config(self) -> "IdpAgentCreate":
        mode = self.extraction_mode
        if mode == "named_config" and not self.field_config_id:
            raise ValueError("field_config_id must be provided when extraction_mode is 'named_config'")
        if mode == "dynamic_prompting" and not self.dynamic_prompt:
            raise ValueError("dynamic_prompt must be provided when extraction_mode is 'dynamic_prompting'")
        return self

class IdpAgentUpdate(BaseModel):
    extraction_mode: str | None = None
    field_config_id: UUID | None = None
    dynamic_prompt: str | None = None
    preprocessing_steps: dict | None = None
    multi_doc_split: bool | None = None
    default_rule_action: str | None = None
    auto_stop_after_batch: bool | None = None
    is_active: bool | None = None
    extra: dict | None = None

class IdpAgentRead(BaseModel):
    id: UUID
    agent_id: UUID
    extraction_mode: str
    field_config_id: UUID | None
    dynamic_prompt: str | None
    preprocessing_steps: dict | None
    multi_doc_split: bool
    default_rule_action: str
    auto_stop_after_batch: bool
    is_active: bool
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _get_user_orgs(session: DbSession, user_id: UUID) -> set[UUID]:
    org_rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.status.in_(["accepted", "active"]),
            )
        )
    ).all()
    return {r if isinstance(r, UUID) else r[0] for r in org_rows}

async def _can_access_agent(session: DbSession, current_user: CurrentActiveUser, agent: Agent) -> bool:
    role = normalize_role(getattr(current_user, "role", None))
    if role == "root":
        return True
    if agent.user_id == current_user.id:
        return True
    if agent.org_id:
        user_orgs = await _get_user_orgs(session, current_user.id)
        if agent.org_id in user_orgs:
            return True
    return False

# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.post("/", response_model=IdpAgentRead, status_code=status.HTTP_201_CREATED)
@router.post("", response_model=IdpAgentRead, status_code=status.HTTP_201_CREATED)
async def create_idp_agent(
    *,
    session: DbSession,
    payload: IdpAgentCreate,
    current_user: CurrentActiveUser,
):
    """Create a new IDP Agent Configuration linked to an existing base Agent."""
    # 1. Fetch base Agent and verify existence/access
    base_agent = await session.get(Agent, payload.agent_id)
    if not base_agent or base_agent.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Base Agent with ID '{payload.agent_id}' not found."
        )

    if not await _can_access_agent(session, current_user, base_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden to this base Agent."
        )

    # 2. Check if an IDP Agent Config already exists for this agent
    existing_stmt = select(IdpAgent).where(IdpAgent.agent_id == payload.agent_id).where(IdpAgent.deleted_at.is_(None))
    existing_idp = (await session.exec(existing_stmt)).first()
    if existing_idp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An active IDP Agent configuration already exists for base Agent '{payload.agent_id}'."
        )

    # 3. Validate extraction mode inputs
    if payload.extraction_mode not in ("dynamic_prompting", "named_config", "multimodal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="extraction_mode must be one of: 'dynamic_prompting', 'named_config', 'multimodal'."
        )

    if payload.default_rule_action not in ("auto_approve", "pending_review"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_rule_action must be one of: 'auto_approve', 'pending_review'."
        )

    if payload.extraction_mode == "named_config" and payload.field_config_id:
        field_cfg = await session.get(IdpFieldConfiguration, payload.field_config_id)
        if not field_cfg or field_cfg.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Linked field configuration '{payload.field_config_id}' not found or deleted."
            )

    # 4. Save to database
    new_idp_agent = IdpAgent(
        agent_id=payload.agent_id,
        extraction_mode=payload.extraction_mode,
        field_config_id=payload.field_config_id,
        dynamic_prompt=payload.dynamic_prompt,
        preprocessing_steps=payload.preprocessing_steps,
        multi_doc_split=payload.multi_doc_split,
        default_rule_action=payload.default_rule_action,
        auto_stop_after_batch=payload.auto_stop_after_batch,
        is_active=payload.is_active,
        extra=payload.extra,
        created_by=current_user.id,
        updated_by=current_user.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(new_idp_agent)
    await session.commit()
    await session.refresh(new_idp_agent)
    return new_idp_agent

@router.get("/", response_model=Page[IdpAgentRead])
@router.get("", response_model=Page[IdpAgentRead])
async def list_idp_agents(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    params: Params = Depends(),
):
    """List all active IDP Agent Configurations scoped by organization membership."""
    role = normalize_role(getattr(current_user, "role", None))
    stmt = select(IdpAgent).where(IdpAgent.deleted_at.is_(None))

    if role != "root":
        user_orgs = await _get_user_orgs(session, current_user.id)
        # Filter where linked Agent org_id is in user_orgs or base agent owner is current_user
        stmt = (
            stmt.join(Agent, Agent.id == IdpAgent.agent_id)
            .where(
                or_(
                    Agent.org_id.in_(user_orgs),
                    Agent.user_id == current_user.id
                )
            )
        )

    return await apaginate(session, stmt, params)

@router.get("/{id}", response_model=IdpAgentRead)
async def get_idp_agent(
    id: UUID,
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Get an IDP Agent Configuration by its UUID or the base Agent UUID."""
    # Find by IDP Agent config UUID or by the base Agent UUID
    stmt = (
        select(IdpAgent)
        .where(or_(IdpAgent.id == id, IdpAgent.agent_id == id))
        .where(IdpAgent.deleted_at.is_(None))
    )
    idp_agent = (await session.exec(stmt)).first()
    if not idp_agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IDP Agent Config with ID/agent_id '{id}' not found."
        )

    # Check access permissions
    base_agent = await session.get(Agent, idp_agent.agent_id)
    if not base_agent or not await _can_access_agent(session, current_user, base_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden to this IDP Agent config."
        )

    return idp_agent

@router.put("/{id}", response_model=IdpAgentRead)
async def update_idp_agent(
    id: UUID,
    *,
    session: DbSession,
    payload: IdpAgentUpdate,
    current_user: CurrentActiveUser,
):
    """Update an IDP Agent Configuration."""
    stmt = (
        select(IdpAgent)
        .where(or_(IdpAgent.id == id, IdpAgent.agent_id == id))
        .where(IdpAgent.deleted_at.is_(None))
    )
    idp_agent = (await session.exec(stmt)).first()
    if not idp_agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IDP Agent Config with ID/agent_id '{id}' not found."
        )

    # Check access permissions
    base_agent = await session.get(Agent, idp_agent.agent_id)
    if not base_agent or not await _can_access_agent(session, current_user, base_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden to update this IDP Agent config."
        )

    # Resolve values (supporting partial updates)
    new_mode = payload.extraction_mode if payload.extraction_mode is not None else idp_agent.extraction_mode
    new_config_id = payload.field_config_id if payload.field_config_id is not None else idp_agent.field_config_id
    new_prompt = payload.dynamic_prompt if payload.dynamic_prompt is not None else idp_agent.dynamic_prompt
    new_default_action = payload.default_rule_action if payload.default_rule_action is not None else idp_agent.default_rule_action

    # Validate updates
    if new_mode not in ("dynamic_prompting", "named_config", "multimodal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="extraction_mode must be one of: 'dynamic_prompting', 'named_config', 'multimodal'."
        )

    if new_default_action not in ("auto_approve", "pending_review"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_rule_action must be one of: 'auto_approve', 'pending_review'."
        )

    if new_mode == "named_config" and not new_config_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="field_config_id must be provided when extraction_mode is 'named_config'."
        )

    if new_mode == "dynamic_prompting" and not new_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="dynamic_prompt must be provided when extraction_mode is 'dynamic_prompting'."
        )

    if payload.field_config_id is not None:
        field_cfg = await session.get(IdpFieldConfiguration, payload.field_config_id)
        if not field_cfg or field_cfg.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Linked field configuration '{payload.field_config_id}' not found or deleted."
            )

    # Apply updates
    if payload.extraction_mode is not None:
        idp_agent.extraction_mode = payload.extraction_mode
    if payload.field_config_id is not None:
        idp_agent.field_config_id = payload.field_config_id
    if payload.dynamic_prompt is not None:
        idp_agent.dynamic_prompt = payload.dynamic_prompt
    if payload.preprocessing_steps is not None:
        idp_agent.preprocessing_steps = payload.preprocessing_steps
    if payload.multi_doc_split is not None:
        idp_agent.multi_doc_split = payload.multi_doc_split
    if payload.default_rule_action is not None:
        idp_agent.default_rule_action = payload.default_rule_action
    if payload.auto_stop_after_batch is not None:
        idp_agent.auto_stop_after_batch = payload.auto_stop_after_batch
    if payload.is_active is not None:
        idp_agent.is_active = payload.is_active
    if payload.extra is not None:
        idp_agent.extra = payload.extra

    idp_agent.updated_by = current_user.id
    idp_agent.updated_at = datetime.now(timezone.utc)

    session.add(idp_agent)
    await session.commit()
    await session.refresh(idp_agent)
    return idp_agent

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_idp_agent(
    id: UUID,
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Soft delete an IDP Agent Configuration."""
    stmt = (
        select(IdpAgent)
        .where(or_(IdpAgent.id == id, IdpAgent.agent_id == id))
        .where(IdpAgent.deleted_at.is_(None))
    )
    idp_agent = (await session.exec(stmt)).first()
    if not idp_agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"IDP Agent Config with ID/agent_id '{id}' not found."
        )

    # Check access permissions
    base_agent = await session.get(Agent, idp_agent.agent_id)
    if not base_agent or not await _can_access_agent(session, current_user, base_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden to delete this IDP Agent config."
        )

    # Perform soft delete
    idp_agent.deleted_at = datetime.now(timezone.utc)
    idp_agent.is_active = False
    idp_agent.updated_by = current_user.id
    idp_agent.updated_at = datetime.now(timezone.utc)

    session.add(idp_agent)
    await session.commit()
    return
