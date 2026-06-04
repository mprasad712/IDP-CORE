import pytest
from uuid import UUID, uuid4
from datetime import datetime, timezone
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
    IdpAgent,
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
            username="std_user@test.com",
            email="std_user@test.com",
            password="testpassword",
            is_active=True,
            is_superuser=False,
            role="developer",
        )
        session.add(std_user)

        # Create organization
        test_org = Organization(
            id=uuid4(),
            name="Test Org Field Configs",
            owner_user_id=root_user.id,
            created_by=root_user.id,
        )
        session.add(test_org)

        # Commit so they have IDs
        await session.commit()
        await session.refresh(std_user)
        await session.refresh(test_org)

        # Resolve the developer role by name (UUIDs differ per environment)
        developer_role = (await session.exec(select(Role).where(Role.name == "developer"))).first()

        # Create user organization membership
        membership = UserOrganizationMembership(
            id=uuid4(),
            user_id=std_user.id,
            org_id=test_org.id,
            status="active",
            role_id=developer_role.id, # developer role
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
            # Delete agents, configs, memberships, orgs, users we created
            # Delete any agent linked to field configs in this org
            configs = (await cleanup_session.exec(select(IdpFieldConfiguration).where(IdpFieldConfiguration.org_id == test_org.id))).all()
            config_ids = [c.id for c in configs]
            if config_ids:
                agents = (await cleanup_session.exec(select(IdpAgent).where(IdpAgent.field_config_id.in_(config_ids)))).all()
                for a in agents:
                    await cleanup_session.delete(a)
                # Headers/line-items are deleted by cascade, but let's delete them just in case
                headers = (await cleanup_session.exec(select(IdpFieldConfigHeader).where(IdpFieldConfigHeader.config_id.in_(config_ids)))).all()
                for h in headers:
                    await cleanup_session.delete(h)
                line_items = (await cleanup_session.exec(select(IdpFieldConfigLineItem).where(IdpFieldConfigLineItem.config_id.in_(config_ids)))).all()
                for li in line_items:
                    await cleanup_session.delete(li)
                for c in configs:
                    await cleanup_session.delete(c)

            # Delete global templates created during test (org_id is None)
            global_configs = (await cleanup_session.exec(select(IdpFieldConfiguration).where(IdpFieldConfiguration.org_id.is_(None)))).all()
            for gc in global_configs:
                if gc.name.startswith("Test Global Template"):
                    headers = (await cleanup_session.exec(select(IdpFieldConfigHeader).where(IdpFieldConfigHeader.config_id == gc.id))).all()
                    for h in headers:
                        await cleanup_session.delete(h)
                    await cleanup_session.delete(gc)

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
async def test_field_configs_flow(setup_test_data):
    global mock_user
    data = setup_test_data
    root_user = data["root_user"]
    std_user = data["std_user"]
    org = data["org"]

    app = create_app()
    app.dependency_overrides[get_current_active_user] = get_mock_user
    # Bypass the Redis-backed idp_rbac router guard under TestClient (its own
    # event loop conflicts with the module-level async Redis pool); endpoint-level
    # scope checks are still exercised. The guard itself is covered by the IDP permissions tests.
    app.dependency_overrides[idp_rbac] = get_mock_user
    client = TestClient(app)

    # ──────────────────────────────────────────────────────────────────
    # 1. POST (Create Field Config) - Root User
    # ──────────────────────────────────────────────────────────────────
    mock_user = root_user

    payload = {
        "name": "Test Configuration One",
        "description": "First test config",
        "org_id": str(org.id),
        "is_template": False,
        "is_active": True,
        "headers": [
            {"field_name": "invoice_no", "field_type": "text", "is_required": True, "display_order": 1},
            {"field_name": "invoice_date", "field_type": "date", "is_required": False, "display_order": 2},
        ]
    }

    response = client.post("/api/v1/idp/field-configs/", json=payload)
    assert response.status_code == 201
    res_data = response.json()
    assert res_data["name"] == "Test Configuration One"
    assert res_data["org_id"] == str(org.id)
    assert len(res_data["headers"]) == 2
    config_id = res_data["id"]

    # Test Duplicate Name
    response = client.post("/api/v1/idp/field-configs/", json=payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # Test Duplicate Headers
    bad_payload = payload.copy()
    bad_payload["name"] = "Different Name"
    bad_payload["headers"] = [
        {"field_name": "dup", "field_type": "text", "display_order": 1},
        {"field_name": "dup", "field_type": "number", "display_order": 2},
    ]
    response = client.post("/api/v1/idp/field-configs/", json=bad_payload)
    assert response.status_code == 400
    assert "Duplicate field names" in response.json()["detail"]

    # ──────────────────────────────────────────────────────────────────
    # 2. POST (Create Global Template) - Root User
    # ──────────────────────────────────────────────────────────────────
    global_payload = {
        "name": "Test Global Template Alpha",
        "description": "Global template description",
        "org_id": None,
        "is_template": True,
        "is_active": True,
        "headers": []
    }
    response = client.post("/api/v1/idp/field-configs/", json=global_payload)
    assert response.status_code == 201
    global_config_id = response.json()["id"]

    # Standard User tries to create global template (org_id = None, is_template = True) -> Should fail with 403
    mock_user = std_user
    response = client.post("/api/v1/idp/field-configs/", json=global_payload)
    assert response.status_code == 403

    # Standard User creates org-specific config (should succeed, auto-resolved to their org if None, or validated if provided)
    std_payload = {
        "name": "Test Configuration Standard User",
        "org_id": str(org.id),
        "is_template": False,
        "headers": []
    }
    response = client.post("/api/v1/idp/field-configs/", json=std_payload)
    assert response.status_code == 201
    std_config_id = response.json()["id"]

    # Standard User tries to create config in another random org -> Should fail with 403
    bad_org_payload = {
        "name": "Bad Org Config",
        "org_id": str(uuid4()),
        "is_template": False,
        "headers": []
    }
    response = client.post("/api/v1/idp/field-configs/", json=bad_org_payload)
    assert response.status_code == 403

    # ──────────────────────────────────────────────────────────────────
    # 3. GET / (List Configs) - Access Control Check
    # ──────────────────────────────────────────────────────────────────
    # Root user can list all
    mock_user = root_user
    response = client.get("/api/v1/idp/field-configs/")
    assert response.status_code == 200
    root_list = response.json()["items"]
    # Should include "Test Configuration One", "Test Global Template Alpha", "Test Configuration Standard User"
    names = [item["name"] for item in root_list]
    assert "Test Configuration One" in names
    assert "Test Global Template Alpha" in names
    assert "Test Configuration Standard User" in names

    # Standard user gets configs: should see their org's configs + global templates, but NOT other orgs
    mock_user = std_user
    response = client.get("/api/v1/idp/field-configs/")
    assert response.status_code == 200
    std_list = response.json()["items"]
    std_names = [item["name"] for item in std_list]
    assert "Test Configuration One" in std_names
    assert "Test Global Template Alpha" in std_names
    assert "Test Configuration Standard User" in std_names

    # List filter test (name contains case-insensitive)
    response = client.get("/api/v1/idp/field-configs/?name=alpha")
    assert response.status_code == 200
    filtered_items = response.json()["items"]
    assert len(filtered_items) == 1
    assert filtered_items[0]["name"] == "Test Global Template Alpha"

    # ──────────────────────────────────────────────────────────────────
    # 4. GET /{id} (Get Details)
    # ──────────────────────────────────────────────────────────────────
    response = client.get(f"/api/v1/idp/field-configs/{config_id}")
    assert response.status_code == 200
    details = response.json()
    assert details["name"] == "Test Configuration One"
    assert len(details["headers"]) == 2
    # Verify display order sorting
    assert details["headers"][0]["field_name"] == "invoice_no"
    assert details["headers"][1]["field_name"] == "invoice_date"

    # Access control: standard user gets a random ID configuration -> Should fail with 404
    response = client.get(f"/api/v1/idp/field-configs/{uuid4()}")
    assert response.status_code == 404

    # ──────────────────────────────────────────────────────────────────
    # 5. PUT /{id} (Update Configuration Metadata)
    # ──────────────────────────────────────────────────────────────────
    mock_user = root_user
    update_payload = {
        "description": "Updated description",
        "is_active": False
    }
    response = client.put(f"/api/v1/idp/field-configs/{config_id}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["description"] == "Updated description"
    assert response.json()["is_active"] is False

    # Standard User tries to edit a global template -> Should fail with 403
    mock_user = std_user
    response = client.put(f"/api/v1/idp/field-configs/{global_config_id}", json=update_payload)
    assert response.status_code == 403

    # ──────────────────────────────────────────────────────────────────
    # 6. Header Sub-resources Endpoints
    # ──────────────────────────────────────────────────────────────────
    mock_user = root_user
    # POST new header
    new_h_payload = {
        "field_name": "vendor_name",
        "field_type": "text",
        "display_order": 3,
        "description": "Name of the vendor"
    }
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/headers", json=new_h_payload)
    assert response.status_code == 201
    new_header_id = response.json()["id"]

    # Verify uniqueness within configuration
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/headers", json=new_h_payload)
    assert response.status_code == 400

    # PUT update header
    up_h_payload = {
        "display_order": 4,
        "is_required": True
    }
    response = client.put(f"/api/v1/idp/field-configs/{config_id}/headers/{new_header_id}", json=up_h_payload)
    assert response.status_code == 200
    assert response.json()["display_order"] == 4
    assert response.json()["is_required"] is True

    # PATCH reorder headers
    # Retrieve configuration headers to reorder
    details_res = client.get(f"/api/v1/idp/field-configs/{config_id}")
    headers_list = details_res.json()["headers"]
    reorder_payload = [
        {"id": h["id"], "display_order": len(headers_list) - idx}
        for idx, h in enumerate(headers_list)
    ]
    response = client.patch(f"/api/v1/idp/field-configs/{config_id}/headers/reorder", json=reorder_payload)
    assert response.status_code == 200

    # GET details again to confirm reorder
    details_res_2 = client.get(f"/api/v1/idp/field-configs/{config_id}")
    sorted_headers = details_res_2.json()["headers"]
    # Check that they are sorted correctly in the returned list
    assert sorted_headers[0]["display_order"] <= sorted_headers[1]["display_order"]

    # DELETE header
    response = client.delete(f"/api/v1/idp/field-configs/{config_id}/headers/{new_header_id}")
    assert response.status_code == 204

    # ──────────────────────────────────────────────────────────────────
    # 6b. Line-Item Sub-resources Endpoints
    # ──────────────────────────────────────────────────────────────────
    mock_user = root_user
    # POST new line-item column
    new_li_payload = {
        "column_name": "item_description",
        "column_type": "text",
        "is_required": True,
        "display_order": 1,
    }
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/line-items", json=new_li_payload)
    assert response.status_code == 201
    new_line_item_id = response.json()["id"]
    assert response.json()["column_name"] == "item_description"

    # Add a second column
    second_li_payload = {
        "column_name": "quantity",
        "column_type": "number",
        "is_required": False,
        "display_order": 2,
    }
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/line-items", json=second_li_payload)
    assert response.status_code == 201
    second_line_item_id = response.json()["id"]

    # Verify uniqueness within configuration
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/line-items", json=new_li_payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # Invalid column_type ('boolean' is NOT allowed for line items) -> 400
    bad_type_payload = {
        "column_name": "is_taxable",
        "column_type": "boolean",
        "display_order": 3,
    }
    response = client.post(f"/api/v1/idp/field-configs/{config_id}/line-items", json=bad_type_payload)
    assert response.status_code == 400
    assert "Invalid column type" in response.json()["detail"]

    # PUT update line-item
    up_li_payload = {
        "display_order": 5,
        "is_required": False,
    }
    response = client.put(
        f"/api/v1/idp/field-configs/{config_id}/line-items/{new_line_item_id}", json=up_li_payload
    )
    assert response.status_code == 200
    assert response.json()["display_order"] == 5
    assert response.json()["is_required"] is False

    # PUT update with invalid type -> 400
    response = client.put(
        f"/api/v1/idp/field-configs/{config_id}/line-items/{new_line_item_id}",
        json={"column_type": "boolean"},
    )
    assert response.status_code == 400

    # PATCH reorder line-items
    li_reorder_payload = [
        {"id": new_line_item_id, "display_order": 1},
        {"id": second_line_item_id, "display_order": 2},
    ]
    response = client.patch(
        f"/api/v1/idp/field-configs/{config_id}/line-items/reorder", json=li_reorder_payload
    )
    assert response.status_code == 200

    # GET details to confirm line_items present and sorted
    details_res_li = client.get(f"/api/v1/idp/field-configs/{config_id}")
    line_items_list = details_res_li.json()["line_items"]
    assert len(line_items_list) == 2
    assert line_items_list[0]["display_order"] <= line_items_list[1]["display_order"]

    # Standard user (no modify rights on this root-owned org config) -> 403 on create
    mock_user = std_user
    response = client.post(
        f"/api/v1/idp/field-configs/{config_id}/line-items",
        json={"column_name": "blocked", "column_type": "text", "display_order": 9},
    )
    assert response.status_code == 403
    mock_user = root_user

    # DELETE line-item
    response = client.delete(f"/api/v1/idp/field-configs/{config_id}/line-items/{second_line_item_id}")
    assert response.status_code == 204

    # Confirm deletion
    details_res_li2 = client.get(f"/api/v1/idp/field-configs/{config_id}")
    assert len(details_res_li2.json()["line_items"]) == 1

    # ──────────────────────────────────────────────────────────────────
    # 7. DELETE (Soft-delete & Blocker Check)
    # ──────────────────────────────────────────────────────────────────
    mock_user = root_user

    # Create dummy IDP Agent linked to std_config_id
    async with session_scope() as session:
        # Create an existing agent record if none exists or use a dummy agent ID
        # Since agent table requires an actual agent to foreign key, we insert a fake agent first or check
        from agentcore.services.database.models.agent.model import Agent
        from agentcore.services.database.models.project.model import Project
        
        # Create dummy project and agent
        proj = Project(id=uuid4(), name="Dummy Project", user_id=std_user.id)
        session.add(proj)
        await session.commit()
        await session.refresh(proj)
        
        dummy_base_agent = Agent(
            id=uuid4(),
            name="Dummy IDP Agent",
            user_id=std_user.id,
            project_id=proj.id,
            data={},
        )
        session.add(dummy_base_agent)
        await session.commit()
        await session.refresh(dummy_base_agent)

        idp_agent = IdpAgent(
            id=uuid4(),
            agent_id=dummy_base_agent.id,
            extraction_mode="named_config",
            field_config_id=std_config_id,
        )
        session.add(idp_agent)
        await session.commit()

    # Try to delete std_config_id -> Should fail with 400 because it is linked to active IDP agent
    response = client.delete(f"/api/v1/idp/field-configs/{std_config_id}")
    assert response.status_code == 400
    assert "linked to one or more active IDP agents" in response.json()["detail"]

    # Now remove the linkage (delete the IDP agent and dummy base agent)
    async with session_scope() as session:
        db_idp_agent = await session.get(IdpAgent, idp_agent.id)
        if db_idp_agent:
            await session.delete(db_idp_agent)
        db_base_agent = await session.get(Agent, dummy_base_agent.id)
        if db_base_agent:
            await session.delete(db_base_agent)
        db_proj = await session.get(Project, proj.id)
        if db_proj:
            await session.delete(db_proj)
        await session.commit()

    # Try deleting standard configuration again -> Should succeed (204)
    response = client.delete(f"/api/v1/idp/field-configs/{std_config_id}")
    assert response.status_code == 204

    # Verify config is soft deleted (deleted_at is set, not returned in list)
    async with session_scope() as session:
        soft_deleted = await session.get(IdpFieldConfiguration, std_config_id)
        assert soft_deleted is not None
        assert soft_deleted.deleted_at is not None

    # Try GET details of soft deleted config -> Should return 404
    response = client.get(f"/api/v1/idp/field-configs/{std_config_id}")
    assert response.status_code == 404

    # Cleanup app dependency overrides
    app.dependency_overrides.clear()


def test_idp_health():
    """The IDP router is mounted and its health endpoint is reachable."""
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/idp/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "IDP feature layer"}
