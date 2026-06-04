from fastapi import APIRouter

router = APIRouter(prefix="/processed-docs", tags=["IDP Processed Documents"])

@router.get("/health")
def health():
    return {"status": "ok"}
