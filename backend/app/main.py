"""
FastAPI application entrypoint.
Run with: uvicorn app.main:app --reload   (from backend/ directory)
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api import jobs, health
from app.utils.logging_config import setup_logging

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

logger = setup_logging("backend")

app = FastAPI(
    title="TranscribeApp API",
    description="Cloud backend for AI transcription + speaker diarization",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # desktop app talks over HTTP directly, not a browser
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(jobs.upload_router)
app.include_router(jobs.router)

if settings.STORAGE_BACKEND == "local":
    import os
    os.makedirs(settings.LOCAL_STORAGE_DIR, exist_ok=True)
    app.mount("/files", StaticFiles(directory=settings.LOCAL_STORAGE_DIR), name="files")


@app.on_event("startup")
def on_startup():
    logger.info("Starting up - initializing database")
    init_db()


@app.get("/")
def root():
    """Serves the simple web UI so anyone can open the backend URL directly in a browser."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"service": "TranscribeApp API", "status": "running", "env": settings.ENV}
