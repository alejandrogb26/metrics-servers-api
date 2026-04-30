import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session, text

from core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/status")
def health_status(session: Session = Depends(get_session)):
    """Endpoint público de diagnóstico. No requiere autenticación."""
    try:
        session.exec(text("SELECT 1")).first()  # type: ignore[call-overload]
        return {"status": "OK", "db": "up"}
    except Exception as exc:
        logger.error("Health check DB error: %s", exc, exc_info=True)
        return {"status": "ERROR"}
