from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
from loguru import logger
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
from agentcore.services.idp.pipeline import _build_llm, PipelineError
from agentcore.services.idp.config_generation import generate_field_config, ConfigGenerationError

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
    prompt: str | None = PydanticField(default=None, description="Extraction prompt / instruction for this field")

class HeaderUpdate(BaseModel):
    field_name: str | None = None
    field_type: str | None = None
    is_required: bool | None = None
    display_order: int | None = None
    description: str | None = None
    prompt: str | None = None

class HeaderRead(BaseModel):
    id: UUID
    config_id: UUID
    field_name: str
    field_type: str
    is_required: bool
    display_order: int
    description: str | None
    prompt: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class LineItemCreate(BaseModel):
    column_name: str = PydanticField(..., description="The name of the line-item column")
    column_type: str = PydanticField(..., description="Type: text, number, or date")
    is_required: bool = PydanticField(default=False, description="Is this column required")
    display_order: int = PydanticField(..., description="The order index for display")
    prompt: str | None = PydanticField(default=None, description="Extraction prompt / instruction for this column")

class LineItemUpdate(BaseModel):
    column_name: str | None = None
    column_type: str | None = None
    is_required: bool | None = None
    display_order: int | None = None
    prompt: str | None = None

class LineItemRead(BaseModel):
    id: UUID
    config_id: UUID
    column_name: str
    column_type: str
    is_required: bool
    display_order: int
    prompt: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class FieldConfigCreate(BaseModel):
    name: str = PydanticField(..., description="Name of the field configuration")
    description: str | None = PydanticField(default=None, description="Optional description")
    doc_type: str | None = PydanticField(default=None, description="Canonical document type this config handles (e.g. 'Invoice')")
    org_id: UUID | None = PydanticField(default=None, description="Organization ID scoping this configuration")
    is_template: bool = PydanticField(default=False, description="Is this configuration a template")
    is_active: bool = PydanticField(default=True, description="Is this configuration active")
    visibility: str = PydanticField(default="org", description="Visibility scope: private | org | dept")
    extra: dict | None = PydanticField(default=None, description="JSON metadata escape-hatch")
    headers: list[HeaderCreate] | None = PydanticField(default=None, description="Nested headers list")

class FieldConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    doc_type: str | None = None
    is_template: bool | None = None
    is_active: bool | None = None
    visibility: str | None = None
    extra: dict | None = None

class FieldConfigRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    doc_type: str | None
    org_id: UUID | None
    is_template: bool
    is_active: bool
    visibility: str
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

class LineItemReorderItem(BaseModel):
    id: UUID
    display_order: int

class TemplateCloneRequest(BaseModel):
    name: str = PydanticField(..., description="The name of the cloned configuration")
    org_id: UUID | None = PydanticField(default=None, description="The target organization UUID")


class GenerateConfigRequest(BaseModel):
    description: str = PydanticField(..., min_length=1, description="Plain-language description of the document type")
    sample_text: str | None = PydanticField(default=None, description="Optional sample OCR text to ground the fields")
    model_id: UUID = PydanticField(..., description="Registry model UUID used to generate the schema")

class GenerateConfigHeader(BaseModel):
    field_name: str
    field_type: str
    is_required: bool
    prompt: str | None
    display_order: int

class GenerateConfigLineItem(BaseModel):
    column_name: str
    column_type: str
    is_required: bool
    prompt: str | None
    display_order: int

class GenerateConfigResponse(BaseModel):
    suggested_name: str
    headers: list[GenerateConfigHeader]
    line_items: list[GenerateConfigLineItem]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

async def _get_scope_memberships(session: DbSession, user_id: UUID) -> tuple[set[UUID], set[UUID]]:
    org_rows = (
        await session.exec(
            select(UserOrganizationMembership.org_id).where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.status.in_(["accepted", "active", "invited"]),
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

    if payload.visibility not in ("private", "org", "dept"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid visibility value. Must be 'private', 'org', or 'dept'.",
        )

    new_config = IdpFieldConfiguration(
        name=payload.name,
        description=payload.description,
        doc_type=payload.doc_type,
        org_id=resolved_org_id,
        is_template=payload.is_template,
        is_active=payload.is_active,
        visibility=payload.visibility,
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
                prompt=h.prompt,
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


@router.post("/generate", response_model=GenerateConfigResponse)
async def generate_config(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    payload: GenerateConfigRequest,
):
    """Generate a DRAFT field configuration from a description (no persistence).

    The caller reviews/edits the returned fields and saves via POST /field-configs/.
    """
    try:
        llm = await _build_llm(session, str(payload.model_id))
    except PipelineError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        draft = await generate_field_config(payload.description, payload.sample_text, llm)
    except ConfigGenerationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("[idp] config generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model service could not generate a configuration.",
        ) from e

    return draft


@router.get("/names", response_model=list[str])
async def list_field_config_names(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Return a sorted list of all field configuration names (custom + templates) accessible to the user.

    Used by the AI Field Extractor node in the builder to populate the
    'Field Configuration' dropdown without needing the full paginated response.
    """
    stmt = (
        select(IdpFieldConfiguration.name)
        .where(
            IdpFieldConfiguration.deleted_at.is_(None),
            IdpFieldConfiguration.is_active == True,
        )
    )

    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    role = normalize_role(getattr(current_user, "role", None))

    if role != "root":
        # Include org-scoped configs AND global templates (accessible to everyone)
        stmt = stmt.where(
            or_(
                IdpFieldConfiguration.org_id.in_(list(org_ids)),
                and_(IdpFieldConfiguration.org_id.is_(None), IdpFieldConfiguration.is_template == True),
            )
        )

    stmt = stmt.order_by(IdpFieldConfiguration.name.asc())
    rows = (await session.exec(stmt)).all()
    return [r for r in rows if r]


@router.get("/doc-types", response_model=list[str])
async def list_field_config_doc_types(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Return a sorted list of unique doc_type values accessible to the user.

    Used by the Document Classifier node to populate the 'Document Types'
    multi-select dropdown — one entry per distinct doc_type on any config the
    user can access (org-scoped configs and global templates).
    """
    stmt = (
        select(IdpFieldConfiguration.doc_type)
        .where(
            IdpFieldConfiguration.deleted_at.is_(None),
            IdpFieldConfiguration.is_active == True,
            IdpFieldConfiguration.doc_type.isnot(None),
        )
    )

    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    role = normalize_role(getattr(current_user, "role", None))

    if role != "root":
        stmt = stmt.where(
            or_(
                IdpFieldConfiguration.org_id.in_(list(org_ids)),
                and_(IdpFieldConfiguration.org_id.is_(None), IdpFieldConfiguration.is_template == True),
            )
        )

    stmt = stmt.distinct().order_by(IdpFieldConfiguration.doc_type.asc())
    rows = (await session.exec(stmt)).all()
    return [r for r in rows if r]


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
    logger.info(f"[list_field_configs] user={current_user.id} role={role} org_ids={org_ids} is_template={is_template}")

    if role != "root":
        if org_id:
            if org_id not in org_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not authorized to access configurations for this organization.",
                )
            stmt = stmt.where(
                or_(
                    IdpFieldConfiguration.created_by == current_user.id,
                    IdpFieldConfiguration.org_id == org_id,
                    and_(IdpFieldConfiguration.org_id.is_(None), IdpFieldConfiguration.is_template == True),
                )
            )
        else:
            # Build org condition only when the user has org memberships
            org_conditions = (
                [IdpFieldConfiguration.org_id.in_(list(org_ids))] if org_ids else []
            )
            stmt = stmt.where(
                or_(
                    IdpFieldConfiguration.created_by == current_user.id,
                    *org_conditions,
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


@router.post("/templates/{template_id}/clone", response_model=FieldConfigRead, status_code=status.HTTP_201_CREATED)
async def clone_template(
    *,
    session: DbSession,
    template_id: UUID,
    payload: TemplateCloneRequest,
    current_user: CurrentActiveUser,
):
    """Clone a global template into a custom organization-scoped field configuration."""
    stmt = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == template_id, IdpFieldConfiguration.deleted_at.is_(None))
    )
    template = (await session.exec(stmt)).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template configuration not found")

    if not template.is_template:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Configuration is not a template")

    org_ids, _ = await _get_scope_memberships(session, current_user.id)
    user_role = normalize_role(getattr(current_user, "role", None))

    resolved_org_id = payload.org_id
    if user_role != "root":
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
                    detail="Not authorized to configure for this organization.",
                )
    else:
        if resolved_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Must specify a target organization ID for cloning templates.",
            )

    # Check target name uniqueness in that organization scope
    stmt_unique = select(IdpFieldConfiguration).where(
        IdpFieldConfiguration.name == payload.name,
        IdpFieldConfiguration.org_id == resolved_org_id,
        IdpFieldConfiguration.deleted_at.is_(None),
    )
    existing = (await session.exec(stmt_unique)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field configuration name already exists in this organization scope.",
        )

    new_config = IdpFieldConfiguration(
        name=payload.name,
        description=template.description,
        doc_type=template.doc_type or template.name,
        org_id=resolved_org_id,
        is_template=False,
        is_active=True,
        visibility="org",
        extra=template.extra,
        created_by=current_user.id,
        updated_by=current_user.id,
    )

    new_config.headers = [
        IdpFieldConfigHeader(
            field_name=h.field_name,
            field_type=h.field_type,
            is_required=h.is_required,
            display_order=h.display_order,
            description=h.description,
            prompt=h.prompt,
        )
        for h in template.headers
    ]

    new_config.line_items = [
        IdpFieldConfigLineItem(
            column_name=l.column_name,
            column_type=l.column_type,
            is_required=l.is_required,
            display_order=l.display_order,
            prompt=l.prompt,
        )
        for l in template.line_items
    ]

    session.add(new_config)
    await session.commit()

    stmt_reload = (
        select(IdpFieldConfiguration)
        .options(selectinload(IdpFieldConfiguration.headers), selectinload(IdpFieldConfiguration.line_items))
        .where(IdpFieldConfiguration.id == new_config.id)
    )
    config_db = (await session.exec(stmt_reload)).first()
    config_db.headers = sorted(config_db.headers, key=lambda h: h.display_order)
    config_db.line_items = sorted(config_db.line_items, key=lambda l: l.display_order)
    return config_db


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
        prompt=payload.prompt,
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


# ──────────────────────────────────────────────────────────────────────
# Line-Item Sub-Resources Endpoints
# ──────────────────────────────────────────────────────────────────────

# Line-item columns only support text/number/date (no boolean), matching the
# idp_field_config_line_items CHECK constraint.
_LINE_ITEM_TYPES = ('text', 'number', 'date')


@router.post("/{id}/line-items", response_model=LineItemRead, status_code=status.HTTP_201_CREATED)
async def create_line_item(
    *,
    session: DbSession,
    id: UUID,
    payload: LineItemCreate,
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

    # Check column name uniqueness within the configuration
    existing = (
        await session.exec(
            select(IdpFieldConfigLineItem).where(
                IdpFieldConfigLineItem.config_id == id, IdpFieldConfigLineItem.column_name == payload.column_name
            )
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Line-item column name already exists in this configuration.",
        )

    # Valid column_type check
    if payload.column_type not in _LINE_ITEM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid column type. Must be 'text', 'number', or 'date'.",
        )

    new_line_item = IdpFieldConfigLineItem(
        config_id=id,
        column_name=payload.column_name,
        column_type=payload.column_type,
        is_required=payload.is_required,
        display_order=payload.display_order,
        prompt=payload.prompt,
    )
    session.add(new_line_item)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    await session.refresh(new_line_item)
    return new_line_item


@router.put("/{id}/line-items/{line_item_id}", response_model=LineItemRead, status_code=status.HTTP_200_OK)
async def update_line_item(
    *,
    session: DbSession,
    id: UUID,
    line_item_id: UUID,
    payload: LineItemUpdate,
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

    line_item = (
        await session.exec(
            select(IdpFieldConfigLineItem).where(
                IdpFieldConfigLineItem.id == line_item_id, IdpFieldConfigLineItem.config_id == id
            )
        )
    ).first()
    if not line_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Line-item column not found in this configuration."
        )

    line_item_data = payload.model_dump(exclude_unset=True)
    if "column_name" in line_item_data and line_item_data["column_name"] != line_item.column_name:
        # Check uniqueness
        existing = (
            await session.exec(
                select(IdpFieldConfigLineItem).where(
                    IdpFieldConfigLineItem.config_id == id,
                    IdpFieldConfigLineItem.column_name == line_item_data["column_name"],
                    IdpFieldConfigLineItem.id != line_item_id,
                )
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Line-item column name already exists in this configuration.",
            )

    if "column_type" in line_item_data and line_item_data["column_type"] not in _LINE_ITEM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid column type. Must be 'text', 'number', or 'date'.",
        )

    for key, value in line_item_data.items():
        setattr(line_item, key, value)

    line_item.updated_at = datetime.now(timezone.utc)
    session.add(line_item)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    await session.refresh(line_item)
    return line_item


@router.delete("/{id}/line-items/{line_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line_item(
    *,
    session: DbSession,
    id: UUID,
    line_item_id: UUID,
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

    line_item = (
        await session.exec(
            select(IdpFieldConfigLineItem).where(
                IdpFieldConfigLineItem.id == line_item_id, IdpFieldConfigLineItem.config_id == id
            )
        )
    ).first()
    if not line_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Line-item column not found in this configuration."
        )

    await session.delete(line_item)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{id}/line-items/reorder", status_code=status.HTTP_200_OK)
async def reorder_line_items(
    *,
    session: DbSession,
    id: UUID,
    payload: list[LineItemReorderItem],
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
        line_item = (
            await session.exec(
                select(IdpFieldConfigLineItem).where(
                    IdpFieldConfigLineItem.id == item.id, IdpFieldConfigLineItem.config_id == id
                )
            )
        ).first()
        if not line_item:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Line item with ID {item.id} not found in this configuration.",
            )
        line_item.display_order = item.display_order
        session.add(line_item)

    config.updated_at = datetime.now(timezone.utc)
    config.updated_by = current_user.id
    session.add(config)

    await session.commit()
    return {"message": "Line items reordered successfully."}
