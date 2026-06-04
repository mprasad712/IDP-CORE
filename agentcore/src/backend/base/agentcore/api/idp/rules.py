from fastapi import APIRouter

router = APIRouter(prefix="/rules", tags=["IDP Rules"])

@router.get("/health")
def health():
    return {"status": "ok"}
