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
from agentcore.services.database.models.idp.config import (
    IdpFieldConfiguration,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
)
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
    """Sets up a test organization, root user, standard user, and memberships."""
    async with session_scope() as session:
        # Check if root user exists or create a test one
        root_user = (await session.exec(select(User).where(User.username == "idp_root_admin@example.com"))).first()
        if not root_user:
            root_user = User(
                id=uuid4(),
                username="idp_root_admin@example.com",
                email="idp_root_admin@example.com",
                password="testpassword",
                is_active=True,
                is_superuser=True,
                role="root",
            )
            session.add(root_user)
            await session.commit()
            await session.refresh(root_user)

        # Create standard user
        std_user = User(
            id=uuid4(),
            username="std_user_catalogue@test.com",
            email="std_user_catalogue@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="idp_configurator",
        )
        session.add(std_user)

        # Create organization
        test_org = Organization(
            id=uuid4(),
            name="Test Org Catalogue",
            owner_user_id=root_user.id,
            created_by=root_user.id,
        )
        session.add(test_org)

        # Commit so they have IDs
        await session.commit()
        await session.refresh(std_user)
        await session.refresh(test_org)

        # Resolve the developer role by name
        developer_role = (await session.exec(select(Role).where(Role.name == "idp_configurator"))).first()

        # Create user organization membership
        membership = UserOrganizationMembership(
            id=uuid4(),
            user_id=std_user.id,
            org_id=test_org.id,
            status="active",
            role_id=developer_role.id,
        )
        session.add(membership)
        await session.commit()

        yield {
            "root_user": root_user,
            "std_user": std_user,
            "org": test_org,
        }

        # Cleanup test data after test runs
        async with session_scope() as cleanup_session:
            # Delete configs in this org
            configs = (await cleanup_session.exec(select(IdpFieldConfiguration).where(IdpFieldConfiguration.org_id == test_org.id))).all()
            config_ids = [c.id for c in configs]
            if config_ids:
                headers = (await cleanup_session.exec(select(IdpFieldConfigHeader).where(IdpFieldConfigHeader.config_id.in_(config_ids)))).all()
                for h in headers:
                    await cleanup_session.delete(h)
                line_items = (await cleanup_session.exec(select(IdpFieldConfigLineItem).where(IdpFieldConfigLineItem.config_id.in_(config_ids)))).all()
                for li in line_items:
                    await cleanup_session.delete(li)
                for c in configs:
                    await cleanup_session.delete(c)

            # Delete memberships, org, user
            m_rows = (await cleanup_session.exec(select(UserOrganizationMembership).where(UserOrganizationMembership.user_id == std_user.id))).all()
            for m in m_rows:
                await cleanup_session.delete(m)
            
            db_org = await cleanup_session.get(Organization, test_org.id)
            if db_org:
                await cleanup_session.delete(db_org)
                
            db_user = await cleanup_session.get(User, std_user.id)
            if db_user:
                await cleanup_session.delete(db_user)
            await cleanup_session.commit()

@pytest.mark.anyio
async def test_catalogue_seeding_and_cloning(setup_test_data):
    global mock_user
    data = setup_test_data
    root_user = data["root_user"]
    std_user = data["std_user"]
    org = data["org"]

    # Initialize the app which triggers the startup lifespan (and seeds our templates)
    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    app.dependency_overrides[idp_rbac] = get_mock_user
    
    client = TestClient(app)

    # 1. Verify standard templates have been seeded
    async with session_scope() as session:
        # Check that we have seeded global templates (Invoice, etc.)
        stmt = select(IdpFieldConfiguration).where(
            IdpFieldConfiguration.is_template == True,
            IdpFieldConfiguration.org_id.is_(None)
        )
        templates = (await session.exec(stmt)).all()
        assert len(templates) >= 30, f"Expected 30+ templates, got {len(templates)}"

        # Verify a specific one like Invoice exists and has headers & line items
        invoice_temp = (await session.exec(
            select(IdpFieldConfiguration).where(
                IdpFieldConfiguration.name == "Invoice",
                IdpFieldConfiguration.is_template == True
            )
        )).first()
        assert invoice_temp is not None
        assert invoice_temp.description is not None
        
        # Verify children are present (we can query database directly)
        headers = (await session.exec(
            select(IdpFieldConfigHeader).where(IdpFieldConfigHeader.config_id == invoice_temp.id)
        )).all()
        assert len(headers) > 0
        line_items = (await session.exec(
            select(IdpFieldConfigLineItem).where(IdpFieldConfigLineItem.config_id == invoice_temp.id)
        )).all()
        assert len(line_items) > 0

    # 2. Test GET list of templates via API (is_template filter is built-in)
    mock_user = std_user
    response = client.get("/api/v1/idp/field-configs/?is_template=true")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["total"] >= 30
    
    # 3. Test POST clone - Happy Path as Standard User
    invoice_template_id = str(invoice_temp.id)
    payload = {
        "name": "Cloned Invoice Config",
        "org_id": str(org.id)
    }
    
    response = client.post(f"/api/v1/idp/field-configs/templates/{invoice_template_id}/clone", json=payload)
    assert response.status_code == 201
    cloned_data = response.json()
    
    assert cloned_data["name"] == "Cloned Invoice Config"
    assert cloned_data["is_template"] is False
    assert cloned_data["org_id"] == str(org.id)
    assert len(cloned_data["headers"]) == len(headers)
    assert len(cloned_data["line_items"]) == len(line_items)
    
    # Check display orders are kept sorted
    for i, h in enumerate(cloned_data["headers"]):
        if i > 0:
            assert h["display_order"] >= cloned_data["headers"][i-1]["display_order"]

    # 4. Test Name Uniqueness in Organization Scope
    response = client.post(f"/api/v1/idp/field-configs/templates/{invoice_template_id}/clone", json=payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # 5. Test Clone on Invalid template_id (404)
    rand_uuid = str(uuid4())
    response = client.post(f"/api/v1/idp/field-configs/templates/{rand_uuid}/clone", json=payload)
    assert response.status_code == 404

    # 6. Test Clone on config that is not a template
    cloned_id = cloned_data["id"]
    payload_new = {
        "name": "Another Cloned Config",
        "org_id": str(org.id)
    }
    response = client.post(f"/api/v1/idp/field-configs/templates/{cloned_id}/clone", json=payload_new)
    assert response.status_code == 400
    assert "not a template" in response.json()["detail"]

    # 7. Test Clone omitting org_id as standard user (defaults to user's org)
    payload_no_org = {
        "name": "Cloned Invoice No Org Explicit"
    }
    response = client.post(f"/api/v1/idp/field-configs/templates/{invoice_template_id}/clone", json=payload_no_org)
    assert response.status_code == 201
    cloned_data_no_org = response.json()
    assert cloned_data_no_org["org_id"] == str(org.id)
