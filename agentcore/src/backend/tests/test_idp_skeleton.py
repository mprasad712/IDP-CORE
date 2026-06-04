from fastapi.testclient import TestClient
from agentcore.main import create_app

def test_idp_health():
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/idp/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "IDP feature layer"}
