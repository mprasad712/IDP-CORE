from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Response
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import select

from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.idp.config import IdpAgentRule, IdpAgent

router = APIRouter(tags=["IDP Rules"])

# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    rule_group: int = PydanticField(..., description="The group identifier for the rule")
    condition_type: str = PydanticField(..., description="Rule condition type")
    field_name: str | None = PydanticField(default=None, description="The name of the header or column field")
    operator: str = PydanticField(..., description="Operator (e.g. ==, >=, <=, >, <, !=, contains, matches)")
    value: str | None = PydanticField(default=None, description="Target value for check")
    field_b: str | None = PydanticField(default=None, description="Optional secondary field name for comparison")
    pattern: str | None = PydanticField(default=None, description="Optional regex pattern")
    combinator: str = PydanticField(default="AND", description="AND or OR combinator for rule grouping")
    action: str = PydanticField(..., description="Action to perform: auto_approve or pending_review")
    display_order: int = PydanticField(..., description="The order index for display / evaluation")
    extra: dict | None = PydanticField(default=None, description="JSON escape hatch")

class RuleUpdate(BaseModel):
    rule_group: int | None = None
    condition_type: str | None = None
    field_name: str | None = None
    operator: str | None = None
    value: str | None = None
    field_b: str | None = None
    pattern: str | None = None
    combinator: str | None = None
    action: str | None = None
    display_order: int | None = None
    extra: dict | None = None

class RuleRead(BaseModel):
    id: UUID
    idp_agent_id: UUID
    rule_group: int
    condition_type: str
    field_name: str | None
    operator: str
    value: str | None
    field_b: str | None
    pattern: str | None
    combinator: str
    action: str
    display_order: int
    extra: dict | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# ──────────────────────────────────────────────────────────────────────
# Validations
# ──────────────────────────────────────────────────────────────────────

ALLOWED_CONDITION_TYPES = {
    'confidence_overall', 'confidence_field', 'field_value_numeric', 'field_value_text',
    'field_value_date', 'field_comparison', 'field_presence', 'pattern_regex', 'visual_element'
}

def validate_rule_data(condition_type: str | None, combinator: str | None, action: str | None):
    if condition_type is not None and condition_type not in ALLOWED_CONDITION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid condition_type '{condition_type}'. Must be one of: {', '.join(ALLOWED_CONDITION_TYPES)}"
        )
    if combinator is not None and combinator not in ('AND', 'OR'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid combinator '{combinator}'. Must be 'AND' or 'OR'."
        )
    if action is not None and action not in ('auto_approve', 'pending_review'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action '{action}'. Must be 'auto_approve' or 'pending_review'."
        )

# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.post("/idp-agents/{idp_agent_id}/rules", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
async def create_agent_rule(
    *,
    session: DbSession,
    idp_agent_id: UUID,
    payload: RuleCreate,
    current_user: CurrentActiveUser,
):
    """Create a new rule condition for a specific IDP Agent."""
    # Check if IDP Agent exists
    agent = (await session.exec(select(IdpAgent).where(IdpAgent.id == idp_agent_id, IdpAgent.deleted_at.is_(None)))).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IDP Agent not found")

    validate_rule_data(payload.condition_type, payload.combinator, payload.action)

    new_rule = IdpAgentRule(
        idp_agent_id=idp_agent_id,
        rule_group=payload.rule_group,
        condition_type=payload.condition_type,
        field_name=payload.field_name,
        operator=payload.operator,
        value=payload.value,
        field_b=payload.field_b,
        pattern=payload.pattern,
        combinator=payload.combinator,
        action=payload.action,
        display_order=payload.display_order,
        extra=payload.extra,
    )
    session.add(new_rule)
    await session.commit()
    await session.refresh(new_rule)
    return new_rule


@router.get("/idp-agents/{idp_agent_id}/rules", response_model=list[RuleRead])
async def list_agent_rules(
    *,
    session: DbSession,
    idp_agent_id: UUID,
    current_user: CurrentActiveUser,
):
    """Retrieve all rules for a specific IDP Agent, ordered by rule_group and display_order."""
    agent = (await session.exec(select(IdpAgent).where(IdpAgent.id == idp_agent_id, IdpAgent.deleted_at.is_(None)))).first()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IDP Agent not found")

    stmt = select(IdpAgentRule).where(IdpAgentRule.idp_agent_id == idp_agent_id).order_by(
        IdpAgentRule.rule_group.asc(),
        IdpAgentRule.display_order.asc()
    )
    rules = (await session.exec(stmt)).all()
    return rules


@router.get("/rules/{rule_id}", response_model=RuleRead)
async def get_rule(
    *,
    session: DbSession,
    rule_id: UUID,
    current_user: CurrentActiveUser,
):
    """Retrieve details of a specific rule condition."""
    rule = await session.get(IdpAgentRule, rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule condition not found")
    return rule


@router.put("/rules/{rule_id}", response_model=RuleRead)
async def update_rule(
    *,
    session: DbSession,
    rule_id: UUID,
    payload: RuleUpdate,
    current_user: CurrentActiveUser,
):
    """Update properties of a specific rule condition."""
    rule = await session.get(IdpAgentRule, rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule condition not found")

    update_data = payload.model_dump(exclude_unset=True)
    
    validate_rule_data(
        update_data.get("condition_type"),
        update_data.get("combinator"),
        update_data.get("action")
    )

    for key, value in update_data.items():
        setattr(rule, key, value)

    rule.updated_at = datetime.now(timezone.utc)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    *,
    session: DbSession,
    rule_id: UUID,
    current_user: CurrentActiveUser,
):
    """Delete a specific rule condition."""
    rule = await session.get(IdpAgentRule, rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule condition not found")

    await session.delete(rule)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
