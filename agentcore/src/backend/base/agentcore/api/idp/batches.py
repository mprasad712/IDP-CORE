from fastapi import APIRouter

router = APIRouter(prefix="/batches", tags=["IDP Batches"])

@router.get("/health")
def health():
    return {"status": "ok"}
