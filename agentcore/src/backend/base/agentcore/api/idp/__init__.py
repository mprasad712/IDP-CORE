from fastapi import APIRouter
from agentcore.api.idp.field_configs import router as field_configs_router
from agentcore.api.idp.documents import router as documents_router
from agentcore.api.idp.jobs import router as jobs_router
from agentcore.api.idp.processed_docs import router as processed_docs_router
from agentcore.api.idp.batches import router as batches_router
from agentcore.api.idp.rules import router as rules_router
from agentcore.api.idp.idp_agents import router as idp_agents_router

router = APIRouter(prefix="/v1/idp", tags=["IDP"])

@router.get("/health")
def health():
    return {"status": "ok", "service": "IDP feature layer"}

router.include_router(field_configs_router)
router.include_router(documents_router)
router.include_router(jobs_router)
router.include_router(processed_docs_router)
router.include_router(batches_router)
router.include_router(rules_router)
router.include_router(idp_agents_router)
