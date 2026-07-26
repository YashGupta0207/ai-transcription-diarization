"""Shared FastAPI dependencies: DB session + desktop API-key auth."""
from fastapi import Header, HTTPException, status

from app.config import settings


def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    """
    Simple shared-secret auth between the desktop client and backend.
    For multi-user production use, replace with per-user JWT/OAuth (see Users model note).
    """
    if not x_api_key or x_api_key != settings.DESKTOP_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    return True
