from fastapi import APIRouter

router = APIRouter(prefix="/idp-agents", tags=["IDP Agents"])

@router.get("/health")
def health():
    return {"status": "ok"}
