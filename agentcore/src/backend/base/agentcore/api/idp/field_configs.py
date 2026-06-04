from fastapi import APIRouter

router = APIRouter(prefix="/field-configs", tags=["IDP Field Configurations"])

@router.get("/health")
def health():
    return {"status": "ok"}
