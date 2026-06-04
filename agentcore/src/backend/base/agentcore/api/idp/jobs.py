from fastapi import APIRouter

router = APIRouter(prefix="/jobs", tags=["IDP Jobs"])

@router.get("/health")
def health():
    return {"status": "ok"}
