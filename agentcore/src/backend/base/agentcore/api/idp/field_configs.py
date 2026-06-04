from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import apaginate
from sqlmodel import select, or_, and_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field as PydanticField

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.config import (
    IdpFieldConfiguration,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
    IdpAgent,
)
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.user_department_membership.model import UserDepartmentMembership
from agentcore.services.auth.permissions import normalize_role

router = APIRouter(prefix="/field-configs", tags=["IDP Field Configurations"])

# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

class HeaderCreate(BaseModel):
    field_name: str = PydanticField(..., description="The name of the header field")
    field_type: str = PydanticField(..., description="Type: text, number, date, or boolean")
    is_required: bool = PydanticField(default=False, description="Is this field required")
    display_order: int = PydanticField(..., description="The order index for display")
    description: str | None = PydanticField(default=None, description="Optional description")

class HeaderUpdate(BaseModel):
    field_name: str | None = None
    field_type: str | None = None
    is_required: bool | None = None
    display_order: int | None = None
    description: str | None = None

class HeaderRead(BaseModel):
    id: UUID
    config_id: UUID
    field_name: str
    field_type: str
    is_required: bool
    display_order: int
    description: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class LineItemRead(BaseModel):
    id: UUID
    config_id: UUID
    column_name: str
    column_type: str
    is_required: bool
    display_order: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class FieldConfigCreate(BaseModel):
    name: str = PydanticField(..., description="Name of the field configuration")
    description: str | None = PydanticField(default=None, description="Optional description")
    org_id: UUID | None = PydanticField(default=None, description="Organization ID scoping this configuration")
    is_template: bool = PydanticField(default=False, description="Is this configuration a template")
    is_active: bool = PydanticField(default=True, description="Is this configuration active")
    extra: dict | None = PydanticField(default=None, description="JSON metadata escape-hatch")
    headers: list[HeaderCreate] | None = PydanticField(default=None, description="Nested headers list")

class FieldConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_template: bool | None = None
    is_active: bool | None = None
    extra: dict | None = None

class FieldConfigRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    org_id: UUID | None
    is_template: bool
    is_active: bool
    deleted_at: datetime | None
    extra: dict | None
    created_by: UUID | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime
    headers: list[HeaderRead] = []
    line_items: list[LineItemRead] = []

    class Config:
        from_attributes = True

class HeaderReorderItem(BaseModel):
    id: UUID
    display_order: int

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _get_scope_memberships(session: DbSession, user_id: UUID) -> tuple[set[UUID], set[UUID]]:
    org_rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.status.in_(["accepted", "active"]),
            )
        )
    ).all()
    dept_rows = (
        await session.exec(
            select(UserDepartmentMembership.department_id).where(
                UserDepartmentMembership.user_id == user_id,
                UserDepartmentMembership.status == "active",
            )
        )
    ).all()
    org_ids = {r if isinstance(r, UUID) else r[0] for r in org_rows}
    dept_ids = {r if isinstance(r, UUID) else r[0] for r in dept_rows}
    return org_ids, dept_ids

async def _can_access_config(session: DbSession, current_user: CurrentActiveUser, config: IdpFieldConfiguration) -> bool:
    role = normalize_role(getattr(current_user, "role", None))
    if role == "root":
        return True
    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    if config.org_id and config.org_id in org_ids:
        return True
    if config.org_id is None and config.is_template:
        return True
    return False

async def _can_modify_config(session: DbSession, current_user: CurrentActiveUser, config: IdpFieldConfiguration) -> bool:
    role = normalize_role(getattr(current_user, "role", None))
    if role == "root":
        return True
    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    if config.org_id is None:
        return False  # Only root can modify global configurations
    if config.org_id in org_ids:
        if role in {"super_admin", "admin"} or config.created_by == current_user.id:
            return True
    return False

# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.post("/", response_model=FieldConfigRead, status_code=status.HTTP_201_CREATED)
async def create_field_config(
    *,
    session: DbSession,
    payload: FieldConfigCreate,
    current_user: CurrentActiveUser,
):
    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    user_role = normalize_role(getattr(current_user, "role", None))

    resolved_org_id = payload.org_id
    if user_role != "root":
        if payload.is_template and resolved_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Root Administrators can create global templates.",
            )
        if resolved_org_id is None:
            if not org_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No active organization mapping found for user.",
                )
            resolved_org_id = sorted(org_ids, key=str)[0]
        else:
            if resolved_org_id not in org_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to create configuration for this organization.",
                )

    # Check name uniqueness
    stmt = select(IdpFieldConfiguration).where(
        IdpFieldConfiguration.name == payload.name,
        IdpFieldConfiguration.deleted_at.is_(None),
    )
    if resolved_org_id is not None:
        stmt = stmt.where(IdpFieldConfiguration.org_id == resolved_org_id)
    else:
        stmt = stmt.where(IdpFieldConfiguration.org_id.is_(None))

    existing = (await session.exec(stmt)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field configuration name already exists in this organization scope.",
        )

    # Validate duplicate names in payload headers
    if payload.headers:
        header_names = [h.field_name for h in payload.headers]
        if len(header_names) != len(set(header_names)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate field names are not allowed in header configurations.",
            )
        for h in payload.headers:
            if h.field_type not in ('text', 'number', 'date', 'boolean'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid field type '{h.field_type}'. Must be text, number, date, or boolean.",
                )

    new_config = IdpFieldConfiguration(
        name=payload.name,
        description=payload.description,
        org_id=resolved_org_id,
        is_template=payload.is_template,
        is_active=payload.is_active,
        extra=payload.extra,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    if payload.headers:
        new_config.headers = [
            IdpFieldConfigHeader(
                field_name=h.field_name,
                field_type=h.field_type,
                is_required=h.is_required,
                display_order=h.display_order,
                description=h.description,
            )
            for h in payload.headers
        ]

    session.add(new_config)
    await session.commit()

    # Re-query to eager-load relationships and sort them
    stmt_reload = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == new_config.id)
    )
    config_db = (await session.exec(stmt_reload)).first()
    config_db.headers = sorted(config_db.headers, key=lambda h: h.display_order)
    config_db.line_items = sorted(config_db.line_items, key=lambda l: l.display_order)
    return config_db


@router.get("/", response_model=Page[FieldConfigRead])
async def list_field_configs(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    params: Params = Depends(),
    name: str | None = None,
    is_template: bool | None = None,
    org_id: UUID | None = None,
    is_active: bool | None = None,
):
    stmt = select(IdpFieldConfiguration).options(
        selectinload(IdpFieldConfiguration.headers),
        selectinload(IdpFieldConfiguration.line_items)
    ).where(IdpFieldConfiguration.deleted_at.is_(None))
    
    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    role = normalize_role(getattr(current_user, "role", None))

    if role != "root":
        if org_id:
            if org_id not in org_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access configurations for this organization.",
                )
            stmt = stmt.where(
                or_(
                    IdpFieldConfiguration.org_id == org_id,
                    and_(IdpFieldConfiguration.org_id.is_(None), IdpFieldConfiguration.is_template == True),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    IdpFieldConfiguration.org_id.in_(list(org_ids)),
                    and_(IdpFieldConfiguration.org_id.is_(None), IdpFieldConfiguration.is_template == True),
                )
            )
    else:
        if org_id:
            stmt = stmt.where(IdpFieldConfiguration.org_id == org_id)

    if name:
        stmt = stmt.where(IdpFieldConfiguration.name.ilike(f"%{name}%"))
    if is_template is not None:
        stmt = stmt.where(IdpFieldConfiguration.is_template == is_template)
    if is_active is not None:
        stmt = stmt.where(IdpFieldConfiguration.is_active == is_active)

    stmt = stmt.order_by(IdpFieldConfiguration.name.asc())

    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, module=r"fastapi_pagination\.ext\.sqlalchemy"
        )
        page_res = await apaginate(session, stmt, params=params)
        
        # Sort nested items deterministically in page results
        for item in page_res.items:
            item.headers = sorted(item.headers, key=lambda h: h.display_order)
            item.line_items = sorted(item.line_items, key=lambda l: l.display_order)
        return page_res


@router.get("/{id}", response_model=FieldConfigRead)
async def get_field_config(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    stmt = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None))
    )
    config = (await session.exec(stmt)).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_access_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    # Sort loaded relationships deterministically
    config.headers = sorted(config.headers, key=lambda h: h.display_order)
    config.line_items = sorted(config.line_items, key=lambda l: l.display_order)
    return config


@router.put("/{id}", response_model=FieldConfigRead)
async def update_field_config(
    *,
    session: DbSession,
    id: UUID,
    payload: FieldConfigUpdate,
    current_user: CurrentActiveUser,
):
    stmt = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None))
    )
    config = (await session.exec(stmt)).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] != config.name:
        # Check name uniqueness
        stmt_unique = select(IdpFieldConfiguration).where(
            IdpFieldConfiguration.name == update_data["name"],
            IdpFieldConfiguration.id != id,
            IdpFieldConfiguration.deleted_at.is_(None),
        )
        if config.org_id is not None:
            stmt_unique = stmt_unique.where(IdpFieldConfiguration.org_id == config.org_id)
        else:
            stmt_unique = stmt_unique.where(IdpFieldConfiguration.org_id.is_(None))

        existing = (await session.exec(stmt_unique)).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Field configuration name already exists in this organization scope.",
            )

    for key, value in update_data.items():
        setattr(config, key, value)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)
    await session.commit()

    # Re-query to eager-load relationships and sort them
    stmt_reload = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == id)
    )
    config_db = (await session.exec(stmt_reload)).first()
    config_db.headers = sorted(config_db.headers, key=lambda h: h.display_order)
    config_db.line_items = sorted(config_db.line_items, key=lambda l: l.display_order)
    return config_db


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field_config(
    *,
    session: DbSession,
    id: UUID,
    current_user: CurrentActiveUser,
):
    stmt = select(IdpFieldConfiguration).where(
        IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None)
    )
    config = (await session.exec(stmt)).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    # Block deletion if linked to an active agent
    agent_stmt = select(IdpAgent).where(IdpAgent.field_config_id == id, IdpAgent.deleted_at.is_(None))
    active_agent = (await session.exec(agent_stmt)).first()
    if active_agent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete field configuration as it is currently linked to one or more active IDP agents.",
        )

    config.deleted_at = datetime.now(timezone.utc)
    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────────────────────────────
# Header Sub-Resources Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.post("/{id}/headers", response_model=HeaderRead, status_code=status.HTTP_201_CREATED)
async def create_header(
    *,
    session: DbSession,
    id: UUID,
    payload: HeaderCreate,
    current_user: CurrentActiveUser,
):
    config = (
        await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None)
            )
        )
    ).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    # Check field name uniqueness within the configuration
    existing = (
        await session.exec(
            select(IdpFieldConfigHeader).where(
                IdpFieldConfigHeader.config_id == id, IdpFieldConfigHeader.field_name == payload.field_name
            )
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header field name already exists in this configuration.",
        )

    # Valid field_type check
    if payload.field_type not in ('text', 'number', 'date', 'boolean'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid field type. Must be 'text', 'number', 'date', or 'boolean'.",
        )

    new_header = IdpFieldConfigHeader(
        config_id=id,
        field_name=payload.field_name,
        field_type=payload.field_type,
        is_required=payload.is_required,
        display_order=payload.display_order,
        description=payload.description,
    )
    session.add(new_header)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    await session.refresh(new_header)
    return new_header


@router.put("/{id}/headers/{header_id}", response_model=HeaderRead, status_code=status.HTTP_200_OK)
async def update_header(
    *,
    session: DbSession,
    id: UUID,
    header_id: UUID,
    payload: HeaderUpdate,
    current_user: CurrentActiveUser,
):
    config = (
        await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None)
            )
        )
    ).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    header = (
        await session.exec(
            select(IdpFieldConfigHeader).where(
                IdpFieldConfigHeader.id == header_id, IdpFieldConfigHeader.config_id == id
            )
        )
    ).first()
    if not header:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Header field not found in this configuration.")

    header_data = payload.model_dump(exclude_unset=True)
    if "field_name" in header_data and header_data["field_name"] != header.field_name:
        # Check uniqueness
        existing = (
            await session.exec(
                select(IdpFieldConfigHeader).where(
                    IdpFieldConfigHeader.config_id == id,
                    IdpFieldConfigHeader.field_name == header_data["field_name"],
                    IdpFieldConfigHeader.id != header_id,
                )
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header field name already exists in this configuration.",
            )

    if "field_type" in header_data and header_data["field_type"] not in ('text', 'number', 'date', 'boolean'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid field type. Must be 'text', 'number', 'date', or 'boolean'.",
        )

    for key, value in header_data.items():
        setattr(header, key, value)

    header.updated_at = datetime.now(timezone.utc)
    session.add(header)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    await session.refresh(header)
    return header


@router.delete("/{id}/headers/{header_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_header(
    *,
    session: DbSession,
    id: UUID,
    header_id: UUID,
    current_user: CurrentActiveUser,
):
    config = (
        await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None)
            )
        )
    ).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    header = (
        await session.exec(
            select(IdpFieldConfigHeader).where(
                IdpFieldConfigHeader.id == header_id, IdpFieldConfigHeader.config_id == id
            )
        )
    ).first()
    if not header:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Header field not found in this configuration.")

    await session.delete(header)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{id}/headers/reorder", status_code=status.HTTP_200_OK)
async def reorder_headers(
    *,
    session: DbSession,
    id: UUID,
    payload: list[HeaderReorderItem],
    current_user: CurrentActiveUser,
):
    config = (
        await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.id == id, IdpFieldConfiguration.deleted_at.is_(None)
            )
        )
    ).first()
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field configuration not found")

    if not await _can_modify_config(session, current_user, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this configuration.")

    for item in payload:
        header = (
            await session.exec(
                select(IdpFieldConfigHeader).where(
                    IdpFieldConfigHeader.id == item.id, IdpFieldConfigHeader.config_id == id
                )
            )
        ).first()
        if not header:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Header with ID {item.id} not found in this configuration.",
            )
        header.display_order = item.display_order
        session.add(header)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    return {"message": "Headers reordered successfully."}
