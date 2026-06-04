from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["IDP Documents"])

@router.get("/health")
def health():
    return {"status": "ok"}
