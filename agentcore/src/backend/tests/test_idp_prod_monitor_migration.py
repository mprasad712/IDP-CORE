"""Unit test for the orphan-gap fix: when a PROD version becomes live (promote / developer-approve),
the live PROD deployment must OWN its background monitors — so control-panel PROD Start/Stop controls
fetching instead of the monitor staying orphaned on the promoted (now inactive) UAT version.

_sync_prod_monitors_for_deployment must: (1) stop the promoted source-UAT version's triggers, and
(2) (re)sync the PROD folder/email/SharePoint/OneDrive monitors for the new deployment. Pure/fast:
the trigger service is mocked.
"""
from uuid import uuid4

import pytest

import agentcore.services.deps as deps
from agentcore.api.publish import _sync_prod_monitors_for_deployment


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _MockTrigSvc:
    def __init__(self):
        self.calls = []

    async def deactivate_triggers_for_deployment(self, session, dep_id):
        self.calls.append(("deactivate", dep_id))

    async def sync_folder_monitors_for_agent(self, **kw):
        self.calls.append(("folder", kw.get("environment"), kw.get("deployment_id")))

    async def sync_email_monitors_for_agent(self, **kw):
        self.calls.append(("email", kw.get("environment"), kw.get("deployment_id")))

    async def sync_sharepoint_idp_monitors_for_agent(self, **kw):
        self.calls.append(("sharepoint", kw.get("environment"), kw.get("deployment_id")))

    async def sync_onedrive_idp_monitors_for_agent(self, **kw):
        self.calls.append(("onedrive", kw.get("environment"), kw.get("deployment_id")))


@pytest.mark.anyio
async def test_stops_source_uat_then_syncs_all_prod_monitors(monkeypatch):
    svc = _MockTrigSvc()
    monkeypatch.setattr(deps, "get_trigger_service", lambda: svc)
    prod_dep, uat_dep = uuid4(), uuid4()

    await _sync_prod_monitors_for_deployment(
        session=object(), agent_id=uuid4(), deployment_id=prod_dep, version="v7",
        snapshot={}, created_by=uuid4(), source_uat_deployment_id=uat_dep,
    )

    kinds = [c[0] for c in svc.calls]
    assert svc.calls[0] == ("deactivate", uat_dep)              # source UAT stopped first
    assert {"folder", "email", "sharepoint", "onedrive"} <= set(kinds)  # all 4 PROD monitors synced
    for c in svc.calls:
        if c[0] in ("folder", "email", "sharepoint", "onedrive"):
            assert c[1] == "prod" and c[2] == prod_dep          # synced for PROD + the new deployment


@pytest.mark.anyio
async def test_no_source_uat_skips_deactivate(monkeypatch):
    svc = _MockTrigSvc()
    monkeypatch.setattr(deps, "get_trigger_service", lambda: svc)
    await _sync_prod_monitors_for_deployment(
        session=object(), agent_id=uuid4(), deployment_id=uuid4(), version="v1",
        snapshot={}, created_by=uuid4(), source_uat_deployment_id=None,
    )
    assert "deactivate" not in [c[0] for c in svc.calls]
    assert len([c for c in svc.calls if c[0] in ("folder", "email", "sharepoint", "onedrive")]) == 4


@pytest.mark.anyio
async def test_one_sync_failure_does_not_abort_the_rest(monkeypatch):
    svc = _MockTrigSvc()

    async def _boom(**kw):
        raise RuntimeError("sync failed")

    svc.sync_email_monitors_for_agent = _boom
    monkeypatch.setattr(deps, "get_trigger_service", lambda: svc)

    # Must not raise; the other monitor types still sync.
    await _sync_prod_monitors_for_deployment(
        session=object(), agent_id=uuid4(), deployment_id=uuid4(), version="v1",
        snapshot={}, created_by=uuid4(),
    )
    assert {"folder", "sharepoint", "onedrive"} <= {c[0] for c in svc.calls}
