"""
Business logic layer sitting between API routes and the repository/queue/storage.
Routes stay thin; this is where orchestration logic lives.
"""
import os
import uuid

from sqlalchemy.orm import Session
from redis import Redis
from rq import Queue

from app.config import settings
from app.repositories.job_repository import JobRepository
from app.storage.factory import get_storage
from app.models.job import JobStatus
from app.utils.media import is_supported

redis_conn = Redis.from_url(settings.REDIS_URL)
job_queue = Queue(settings.QUEUE_NAME, connection=redis_conn)


class JobService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = JobRepository(db)
        self.storage = get_storage()

    def create_job_from_upload(self, filename: str, file_obj, content_type: str, provider: str = None):
        if not is_supported(filename):
            raise ValueError(f"Unsupported file type: {filename}")

        provider = provider or settings.SPEECH_PROVIDER
        job = self.repo.create_job(original_filename=filename, provider=provider)

        ext = os.path.splitext(filename)[1]
        storage_key = f"uploads/{job.id}/{uuid.uuid4().hex}{ext}"

        file_obj.seek(0, os.SEEK_END)
        size_bytes = file_obj.tell()
        file_obj.seek(0)

        self.storage.save(storage_key, file_obj)
        self.repo.attach_file(job, storage_key, settings.STORAGE_BACKEND, content_type, size_bytes)

        # Enqueue background processing - this call returns immediately.
        self.repo.set_status(job, JobStatus.QUEUED)
        job_queue.enqueue(
            "worker.tasks.process_transcription_job",
            job.id,
            job_timeout="30m",
            retry=None,  # explicit retry logic handled inside the task itself
        )
        return job

    def get_job(self, job_id: str):
        return self.repo.get(job_id)

    def list_jobs(self, limit=50, offset=0):
        return self.repo.list_jobs(limit, offset), self.repo.count_jobs()

    def cancel_job(self, job_id: str):
        job = self.repo.get(job_id)
        if not job:
            return None
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job
        return self.repo.set_status(job, JobStatus.CANCELLED)

    def delete_job(self, job_id: str):
        job = self.repo.get(job_id)
        if not job:
            return False
        if job.file:
            try:
                self.storage.delete(job.file.storage_key)
            except Exception:
                pass
        self.repo.delete(job)
        return True

    def queue_length(self) -> int:
        return len(job_queue)

    def failed_count(self) -> int:
        return self.repo.failed_count()

    def avg_processing_time(self):
        return self.repo.avg_processing_seconds()
