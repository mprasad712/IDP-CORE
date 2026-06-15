import pytest
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from sqlmodel import select

from agentcore.main import create_app
from agentcore.services.deps import session_scope
from agentcore.services.auth.utils import get_current_active_user
from agentcore.api.idp import idp_rbac
from agentcore.services.database.models.user.model import User
from agentcore.services.database.models.organization.model import Organization
from agentcore.services.database.models.user_organization_membership.model import UserOrganizationMembership
from agentcore.services.database.models.idp.config import IdpAgent, IdpAgentRule, IdpFieldConfiguration
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.project.model import Project
from agentcore.services.database.models.role.model import Role

# Global test variables to control current user
mock_user = None

def get_mock_user():
    global mock_user
    return mock_user

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def setup_test_data():
    """Sets up a test organization, developer user, project, base agent, and IDP agent."""
    async with session_scope() as session:
        # Resolve developer role
        developer_role = (await session.exec(select(Role).where(Role.name == "idp_configurator"))).first()

        # Create user
        unique_suffix = uuid4().hex[:8]
        user = User(
            id=uuid4(),
            username=f"rules_test_user_{unique_suffix}@test.com",
            email=f"rules_test_user_{unique_suffix}@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="idp_configurator",
        )
        session.add(user)
        await session.flush()

        # Create organization
        org = Organization(
            id=uuid4(),
            name=f"Rules Test Org {unique_suffix}",
            owner_user_id=user.id,
            created_by=user.id,
        )
        session.add(org)
        await session.commit()
        await session.refresh(user)
        await session.refresh(org)

        # Create membership
        membership = UserOrganizationMembership(
            id=uuid4(),
            user_id=user.id,
            org_id=org.id,
            status="active",
            role_id=developer_role.id,
        )
        session.add(membership)

        # Create project
        proj = Project(id=uuid4(), name="Rules Test Project", user_id=user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        # Create base agent
        base_agent = Agent(
            id=uuid4(),
            name="Rules Base Agent",
            user_id=user.id,
            project_id=proj.id,
            org_id=org.id,
            data={},
        )
        session.add(base_agent)
        await session.commit()
        await session.refresh(base_agent)

        # Create IDP agent
        idp_agent = IdpAgent(
            id=uuid4(),
            agent_id=base_agent.id,
            extraction_mode="dynamic_prompting",
            default_rule_action="pending_review",
            is_active=True,
        )
        session.add(idp_agent)
        await session.commit()
        await session.refresh(idp_agent)

        yield {
            "user": user,
            "org": org,
            "idp_agent": idp_agent,
            "base_agent": base_agent,
        }

        # Cleanup test data after test runs
        async with session_scope() as cleanup_session:
            # Delete any agent rules
            rules = (await cleanup_session.exec(select(IdpAgentRule).where(IdpAgentRule.idp_agent_id == idp_agent.id))).all()
            for r in rules:
                await cleanup_session.delete(r)

            # Delete IDP agent
            db_idp_agent = await cleanup_session.get(IdpAgent, idp_agent.id)
            if db_idp_agent:
                await cleanup_session.delete(db_idp_agent)

            # Delete base agent
            db_base_agent = await cleanup_session.get(Agent, base_agent.id)
            if db_base_agent:
                await cleanup_session.delete(db_base_agent)

            # Delete project
            db_proj = await cleanup_session.get(Project, proj.id)
            if db_proj:
                await cleanup_session.delete(db_proj)

            # Delete membership, org, user
            m_rows = (await cleanup_session.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == user.id))).all()
            for m in m_rows:
                await cleanup_session.delete(m)
            
            db_org = await cleanup_session.get(Organization, org.id)
            if db_org:
                await cleanup_session.delete(db_org)
                
            db_user = await cleanup_session.get(User, user.id)
            if db_user:
                await cleanup_session.delete(db_user)
            await cleanup_session.commit()

@pytest.mark.anyio
async def test_rules_crud_flow(setup_test_data):
    global mock_user
    data = setup_test_data
    user = data["user"]
    idp_agent = data["idp_agent"]

    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    
    client = TestClient(app)
    mock_user = user

    # 1. Create Rule condition
    payload = {
        "rule_group": 1,
        "condition_type": "confidence_overall",
        "field_name": None,
        "operator": ">=",
        "value": "0.85",
        "combinator": "AND",
        "action": "auto_approve",
        "display_order": 1,
    }
    
    response = client.post(f"/api/v1/idp/idp-agents/{idp_agent.id}/rules", json=payload)
    assert response.status_code == 201
    rule_data = response.json()
    assert rule_data["operator"] == ">="
    assert rule_data["value"] == "0.85"
    assert rule_data["idp_agent_id"] == str(idp_agent.id)
    rule_id = rule_data["id"]

    # 2. Test Invalid Validations (condition_type)
    bad_payload = payload.copy()
    bad_payload["condition_type"] = "invalid_condition_type_name"
    response = client.post(f"/api/v1/idp/idp-agents/{idp_agent.id}/rules", json=bad_payload)
    assert response.status_code == 400
    assert "Invalid condition_type" in response.json()["detail"]

    # 3. Test Invalid Validations (combinator)
    bad_payload = payload.copy()
    bad_payload["combinator"] = "NOT"
    response = client.post(f"/api/v1/idp/idp-agents/{idp_agent.id}/rules", json=bad_payload)
    assert response.status_code == 400
    assert "Invalid combinator" in response.json()["detail"]

    # 4. Test Invalid Validations (action)
    bad_payload = payload.copy()
    bad_payload["action"] = "auto_reject"
    response = client.post(f"/api/v1/idp/idp-agents/{idp_agent.id}/rules", json=bad_payload)
    assert response.status_code == 400
    assert "Invalid action" in response.json()["detail"]

    # 5. List rules for IDP agent
    response = client.get(f"/api/v1/idp/idp-agents/{idp_agent.id}/rules")
    assert response.status_code == 200
    rules_list = response.json()
    assert len(rules_list) == 1
    assert rules_list[0]["id"] == rule_id

    # 6. Get single rule detail
    response = client.get(f"/api/v1/idp/rules/{rule_id}")
    assert response.status_code == 200
    assert response.json()["id"] == rule_id

    # 7. Get non-existent rule (404)
    rand_uuid = str(uuid4())
    response = client.get(f"/api/v1/idp/rules/{rand_uuid}")
    assert response.status_code == 404

    # 8. Update Rule
    update_payload = {
        "value": "0.90",
        "display_order": 2
    }
    response = client.put(f"/api/v1/idp/rules/{rule_id}", json=update_payload)
    assert response.status_code == 200
    updated_data = response.json()
    assert updated_data["value"] == "0.90"
    assert updated_data["display_order"] == 2

    # 9. Delete Rule
    response = client.delete(f"/api/v1/idp/rules/{rule_id}")
    assert response.status_code == 204

    # Verify deleted
    response = client.get(f"/api/v1/idp/rules/{rule_id}")
    assert response.status_code == 404


# ── Pure-function tests (no DB) for robust numeric rule parsing ──

def test_rules_to_number_handles_money_and_formatting():
    """Numeric rule comparisons must tolerate currency symbols, thousands separators, % and text."""
    from agentcore.services.idp.rules_engine import _to_number
    assert _to_number("1,200.00") == 1200.0
    assert _to_number("$50.00") == 50.0
    assert _to_number("₹ 19.80") == 19.8
    assert _to_number("12.5%") == 12.5
    assert _to_number("USD 1,000") == 1000.0
    assert _to_number(129.8) == 129.8
    # Identifier-like / non-numeric values must be REJECTED (not coerced to a bogus number).
    for bad in (None, "", "abc", "-", "1-2", "INV-123", "N/A", "50-", "12-31-2024", True):
        try:
            _to_number(bad)
            assert False, f"expected ValueError for {bad!r}"
        except (ValueError, TypeError):
            pass


def test_rules_numeric_compare_with_currency():
    """`calculated_total > 0` style rules must pass on real money strings like '$1,200.00'."""
    from agentcore.services.idp.rules_engine import _compare_values
    assert _compare_values("1,200.00", "1000", ">", "numeric") is True
    assert _compare_values("$129.80", "0", ">", "numeric") is True
    assert _compare_values("19.80", "19.80", "==", "numeric") is True
    assert _compare_values("$5.00", "10", ">", "numeric") is False
