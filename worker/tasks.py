"""
RQ task(s) executed by the background worker process.

Why RQ over Celery/Dramatiq/Huey (see README for full comparison):
  - Zero extra infra beyond Redis (which Render's free tier plan supports via
    Render Redis or an external free Redis like Upstash).
  - Much simpler operational model than Celery (no separate beat scheduler,
    no broker/backend split config) - fewer moving parts to break on a free tier.
  - `rq worker` runs as a single lightweight background worker process/dyno,
    which fits Render's "Background Worker" service type exactly.

This module must be importable as `worker.tasks` from wherever RQ is enqueuing
jobs from (see app/services/job_service.py which enqueues by string path so
the API process never needs to import worker code directly).
"""
import os
import sys
import time
import tempfile

# Allow running this worker with backend/app on the path when deployed as a
# separate service that shares the `app` package via the `shared` mechanism.
# Also ensure the project root (parent of this file's directory) is importable
# so `worker.tasks.*` resolves correctly regardless of how the process was
# launched (plain script, `-m`, or from inside Docker's WORKDIR).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..")
sys.path.insert(0, os.path.join(_THIS_DIR, "..", "backend"))
sys.path.insert(0, _PROJECT_ROOT)

from app.database import SessionLocal
from app.repositories.job_repository import JobRepository
from app.models.job import JobStatus
from app.storage.factory import get_storage
from app.providers.factory import get_provider
from app.utils.media import is_video, extract_audio, segments_to_readable
from app.utils.logging_config import setup_logging

logger = setup_logging("worker")

MAX_RETRIES = 3


def process_transcription_job(job_id: str):
    """
    Entry point invoked by RQ. Fully self-contained: opens its own DB session
    (must not share a session across processes), downloads the file from
    storage, extracts audio if needed, calls the configured AI provider,
    persists results, and updates job status at every stage so the desktop
    client's polling always reflects true progress.
    """
    db = SessionLocal()
    repo = JobRepository(db)
    storage = get_storage()

    job = repo.get(job_id)
    if not job:
        logger.error(f"Job {job_id} not found - aborting task")
        db.close()
        return

    if job.status == JobStatus.CANCELLED:
        logger.info(f"Job {job_id} was cancelled before processing started - skipping")
        db.close()
        return

    tmp_files_to_cleanup = []
    try:
        repo.set_status(job, JobStatus.PROCESSING)
        logger.info(f"Job {job_id}: downloading source file")

        local_input_path = storage.get_local_path(job.file.storage_key)
        tmp_files_to_cleanup.append(local_input_path)

        audio_path = local_input_path
        if is_video(job.original_filename):
            logger.info(f"Job {job_id}: extracting audio from video")
            audio_path = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
            extract_audio(local_input_path, audio_path)
            tmp_files_to_cleanup.append(audio_path)

        provider = get_provider(job.provider)
        logger.info(f"Job {job_id}: sending to provider '{job.provider}'")

        result = _call_provider_with_retry(provider, audio_path, job.language, job_id)

        seg_dicts = [
            {
                "speaker": s.speaker,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "confidence": s.confidence,
            }
            for s in result.segments
        ]
        readable = segments_to_readable(seg_dicts)

        repo.save_results(job, seg_dicts, readable, result.raw_response)
        repo.set_status(job, JobStatus.COMPLETED)
        logger.info(f"Job {job_id}: completed successfully")

    except Exception as exc:
        logger.error(f"Job {job_id}: failed - {exc}")
        job = repo.get(job_id)
        if job.retry_count < MAX_RETRIES:
            repo.increment_retry(job)
            repo.set_status(job, JobStatus.RETRYING, error_message=str(exc))
            # Re-enqueue with backoff; a real deployment could use rq's
            # built-in Retry object - kept explicit here for clarity/testability.
            from app.services.job_service import job_queue
            backoff_seconds = 10 * (job.retry_count ** 2)
            job_queue.enqueue_in(
                __import__("datetime").timedelta(seconds=backoff_seconds),
                "worker.tasks.process_transcription_job",
                job_id,
            )
        else:
            repo.set_status(job, JobStatus.FAILED, error_message=str(exc))
    finally:
        for path in tmp_files_to_cleanup:
            try:
                if path and os.path.exists(path) and path != local_input_path:
                    os.remove(path)
            except Exception:
                pass
        db.close()


def _call_provider_with_retry(provider, audio_path: str, language: str, job_id: str, attempts: int = 2):
    """Inline retry specifically for transient provider/network errors within a single task run."""
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return provider.transcribe_and_diarize(audio_path, language)
        except Exception as e:
            last_exc = e
            logger.warning(f"Job {job_id}: provider attempt {attempt}/{attempts} failed - {e}")
            time.sleep(3 * attempt)
    raise last_exc
