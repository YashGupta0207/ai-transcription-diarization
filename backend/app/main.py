"""
FastAPI application entrypoint.
Run with: uvicorn app.main:app --reload   (from backend/ directory)
"""
import os
import threading

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api import jobs, health
from app.utils.logging_config import setup_logging

logger = setup_logging("backend")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _start_inline_worker():
    """
    Runs the RQ worker loop inside this same process/container.
    Used on Render's free tier since Background Worker services there have
    no free instance type (minimum $7/month) - unlike Web Services, which do
    have a free option. Only activates when RUN_WORKER_INLINE=true is set,
    so local Docker Compose (which already runs a separate worker container)
    is unaffected.

    IMPORTANT: RQ uses OS signals in TWO places that only work in a
    process's main thread - both must be disabled since this runs in a
    background thread:
      1. work() unconditionally calls _install_signal_handlers() for
         SIGINT/SIGTERM - overridden to a no-op below.
      2. Per-job timeout enforcement uses SIGALRM via death_penalty_class -
         replaced with a no-op death penalty below. Our own provider calls
         already have their own timeouts (see providers/*.py), so this is
         an acceptable tradeoff for running inside a thread.
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from redis import Redis
    from rq import Queue
    from rq.worker import SimpleWorker
    from rq.timeouts import BaseDeathPenalty
    from app.config import settings as worker_settings

    class NoOpDeathPenalty(BaseDeathPenalty):
        def setup_death_penalty(self):
            pass

        def cancel_death_penalty(self):
            pass

    logger.info("Inline worker thread: connecting to Redis")
    conn = Redis.from_url(worker_settings.REDIS_URL)
    queue = Queue(worker_settings.QUEUE_NAME, connection=conn)

    worker = SimpleWorker([queue], connection=conn)
    worker.death_penalty_class = NoOpDeathPenalty
    worker._install_signal_handlers = lambda: None  # no-op - main-thread only

    logger.info("Inline worker thread: starting work loop")
    worker.work(with_scheduler=True)


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
    os.makedirs(settings.LOCAL_STORAGE_DIR, exist_ok=True)
    app.mount("/files", StaticFiles(directory=settings.LOCAL_STORAGE_DIR), name="files")


@app.on_event("startup")
def on_startup():
    logger.info("Starting up - initializing database")
    init_db()

    if os.environ.get("RUN_WORKER_INLINE", "").lower() == "true":
        logger.info("Starting inline background worker thread")
        thread = threading.Thread(target=_start_inline_worker, daemon=True)
        thread.start()


@app.get("/")
def root():
    """Serves the simple web UI so anyone can open the backend URL directly in a browser."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"service": "TranscribeApp API", "status": "running", "env": settings.ENV}