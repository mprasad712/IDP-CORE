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
from agentcore.services.database.models.idp.config import IdpAgent, IdpFieldConfiguration
from agentcore.services.database.models.agent.model import Agent
from agentcore.services.database.models.project.model import Project
from agentcore.services.database.models.role.model import Role

mock_user = None

def get_mock_user():
    global mock_user
    return mock_user

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def setup_test_data():
    """Sets up user, organization, base agent, and field configuration."""
    async with session_scope() as session:
        # Resolve developer role
        developer_role = (await session.exec(select(Role).where(Role.name == "developer"))).first()

        # Create user
        unique_suffix = uuid4().hex[:8]
        user = User(
            id=uuid4(),
            username=f"agent_test_user_{unique_suffix}@test.com",
            email=f"agent_test_user_{unique_suffix}@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="developer",
        )
        session.add(user)
        await session.flush()

        # Create organization
        org = Organization(
            id=uuid4(),
            name=f"Agent Test Org {unique_suffix}",
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
        proj = Project(id=uuid4(), name="Agent Test Project", user_id=user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)

        # Create base agents
        base_agent = Agent(
            id=uuid4(),
            name="Test Base Agent 1",
            user_id=user.id,
            project_id=proj.id,
            org_id=org.id,
            data={},
        )
        base_agent2 = Agent(
            id=uuid4(),
            name="Test Base Agent 2",
            user_id=user.id,
            project_id=proj.id,
            org_id=org.id,
            data={},
        )
        session.add(base_agent)
        session.add(base_agent2)
        
        # Create a field config template
        field_cfg = IdpFieldConfiguration(
            id=uuid4(),
            name="Test Field Config Template",
            org_id=org.id,
            is_template=False,
        )
        session.add(field_cfg)
        
        await session.commit()
        await session.refresh(base_agent)
        await session.refresh(base_agent2)
        await session.refresh(field_cfg)

        yield {
            "user": user,
            "org": org,
            "base_agent": base_agent,
            "base_agent2": base_agent2,
            "field_cfg": field_cfg,
        }

        # Cleanup test data after test runs
        async with session_scope() as cleanup_session:
            # Delete any created IDP agents
            agents = (await cleanup_session.exec(select(IdpAgent))).all()
            for a in agents:
                await cleanup_session.delete(a)

            db_cfg = await cleanup_session.get(IdpFieldConfiguration, field_cfg.id)
            if db_cfg:
                await cleanup_session.delete(db_cfg)

            db_base_agent = await cleanup_session.get(Agent, base_agent.id)
            if db_base_agent:
                await cleanup_session.delete(db_base_agent)

            db_base_agent2 = await cleanup_session.get(Agent, base_agent2.id)
            if db_base_agent2:
                await cleanup_session.delete(db_base_agent2)

            db_proj = await cleanup_session.get(Project, proj.id)
            if db_proj:
                await cleanup_session.delete(db_proj)

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
async def test_idp_agent_crud_flow(setup_test_data):
    global mock_user
    data = setup_test_data
    user = data["user"]
    base_agent = data["base_agent"]
    base_agent2 = data["base_agent2"]
    field_cfg = data["field_cfg"]

    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    client = TestClient(app)

    # Set mock user
    mock_user = user

    # 1. Create IDP Agent config (success case: dynamic_prompting)
    response = client.post(
        "/api/v1/idp/idp-agents",
        json={
            "agent_id": str(base_agent.id),
            "extraction_mode": "dynamic_prompting",
            "dynamic_prompt": "Extract invoices details",
        }
    )
    assert response.status_code == 201
    res_json = response.json()
    assert res_json["extraction_mode"] == "dynamic_prompting"
    assert res_json["dynamic_prompt"] == "Extract invoices details"
    idp_agent_id = res_json["id"]

    # 2. Duplicate Create should fail
    response = client.post(
        "/api/v1/idp/idp-agents",
        json={
            "agent_id": str(base_agent.id),
            "extraction_mode": "multimodal",
        }
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # 3. Validation failure: named_config mode without field_config_id
    response = client.post(
        "/api/v1/idp/idp-agents",
        json={
            "agent_id": str(base_agent2.id),
            "extraction_mode": "named_config",
        }
    )
    assert response.status_code == 422 # Pydantic validation error

    # 4. Successful named_config creation
    response = client.post(
        "/api/v1/idp/idp-agents",
        json={
            "agent_id": str(base_agent2.id),
            "extraction_mode": "named_config",
            "field_config_id": str(field_cfg.id),
        }
    )
    assert response.status_code == 201
    res_json2 = response.json()
    assert res_json2["extraction_mode"] == "named_config"
    assert res_json2["field_config_id"] == str(field_cfg.id)
    idp_agent2_id = res_json2["id"]

    # 5. List IDP Agents
    response = client.get("/api/v1/idp/idp-agents")
    assert response.status_code == 200
    list_json = response.json()
    assert "items" in list_json
    assert len(list_json["items"]) >= 2
    ids_in_list = [item["id"] for item in list_json["items"]]
    assert idp_agent_id in ids_in_list
    assert idp_agent2_id in ids_in_list

    # 6. Read single IDP Agent (by IDP Agent ID and by base Agent ID)
    response = client.get(f"/api/v1/idp/idp-agents/{idp_agent_id}")
    assert response.status_code == 200
    assert response.json()["id"] == idp_agent_id

    response = client.get(f"/api/v1/idp/idp-agents/{base_agent.id}")
    assert response.status_code == 200
    assert response.json()["id"] == idp_agent_id

    # 7. Update IDP Agent config
    response = client.put(
        f"/api/v1/idp/idp-agents/{idp_agent_id}",
        json={
            "extraction_mode": "multimodal",
            "dynamic_prompt": None,
            "multi_doc_split": True,
        }
    )
    assert response.status_code == 200
    assert response.json()["extraction_mode"] == "multimodal"
    assert response.json()["multi_doc_split"] is True

    # 8. Delete IDP Agent config (soft delete)
    response = client.delete(f"/api/v1/idp/idp-agents/{idp_agent_id}")
    assert response.status_code == 204

    # Get single should now return 404 since it's deleted
    response = client.get(f"/api/v1/idp/idp-agents/{idp_agent_id}")
    assert response.status_code == 404

    app.dependency_overrides.clear()
