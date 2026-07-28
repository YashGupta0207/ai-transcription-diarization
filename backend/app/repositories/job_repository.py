"""Data-access layer for Job/MediaFile/Segment/Speaker/TranscriptWord. Keeps SQLAlchemy queries out of routes."""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus, MediaFile, Segment, Speaker, TranscriptWord


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_job(self, original_filename: str, provider: str, language: str = "en") -> Job:
        job = Job(original_filename=original_filename, provider=provider, language=language,
                  status=JobStatus.UPLOADING)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def attach_file(self, job: Job, storage_key: str, storage_backend: str,
                     content_type: Optional[str], size_bytes: Optional[int]) -> MediaFile:
        media = MediaFile(storage_key=storage_key, storage_backend=storage_backend,
                           content_type=content_type, size_bytes=size_bytes)
        self.db.add(media)
        self.db.flush()
        job.file_id = media.id
        self.db.commit()
        self.db.refresh(job)
        return media

    def get(self, job_id: str) -> Optional[Job]:
        return self.db.query(Job).filter(Job.id == job_id).first()

    def list_jobs(self, limit: int = 50, offset: int = 0) -> List[Job]:
        return (
            self.db.query(Job)
            .order_by(Job.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_jobs(self) -> int:
        return self.db.query(Job).count()

    def set_status(self, job: Job, status: JobStatus, error_message: str = None):
        job.status = status
        now = datetime.utcnow()
        if status == JobStatus.QUEUED:
            job.queued_at = now
        elif status == JobStatus.PROCESSING:
            job.started_at = now
        elif status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            job.completed_at = now
        if error_message is not None:
            job.error_message = error_message
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job

    def increment_retry(self, job: Job):
        job.retry_count += 1
        self.db.commit()
        self.db.refresh(job)
        return job

    def save_results(self, job: Job, segments: List[dict], readable_transcript: str, raw_json: dict,
                      words: Optional[List[dict]] = None):
        # Clear any previous partial segments (retry scenario)
        self.db.query(Segment).filter(Segment.job_id == job.id).delete()

        speakers_seen = {}
        for idx, seg in enumerate(segments):
            self.db.add(Segment(
                job_id=job.id,
                speaker_label=seg["speaker"],
                text=seg["text"],
                start=seg["start"],
                end=seg["end"],
                confidence=seg.get("confidence"),
                order_index=idx,
            ))
            speakers_seen.setdefault(seg["speaker"], True)

        self.db.query(Speaker).filter(Speaker.job_id == job.id).delete()
        for label in speakers_seen:
            self.db.add(Speaker(job_id=job.id, label=label))

        # Word-level timestamps for Playback Verification - additive, does
        # not affect segments/speakers/readable_transcript above in any way.
        self.db.query(TranscriptWord).filter(TranscriptWord.job_id == job.id).delete()
        if words:
            for idx, w in enumerate(words):
                self.db.add(TranscriptWord(
                    job_id=job.id,
                    speaker_label=w["speaker"],
                    word=w["word"],
                    start=w["start"],
                    end=w["end"],
                    confidence=w.get("confidence"),
                    order_index=idx,
                ))

        job.readable_transcript = readable_transcript
        job.raw_provider_json = raw_json
        job.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def delete(self, job: Job):
        self.db.delete(job)
        self.db.commit()

    def get_segments(self, job_id: str) -> List[Segment]:
        return (
            self.db.query(Segment)
            .filter(Segment.job_id == job_id)
            .order_by(Segment.order_index.asc())
            .all()
        )

    def get_words(self, job_id: str) -> List[TranscriptWord]:
        return (
            self.db.query(TranscriptWord)
            .filter(TranscriptWord.job_id == job_id)
            .order_by(TranscriptWord.order_index.asc())
            .all()
        )

    def failed_count(self) -> int:
        return self.db.query(Job).filter(Job.status == JobStatus.FAILED).count()

    def avg_processing_seconds(self) -> Optional[float]:
        completed = (
            self.db.query(Job)
            .filter(Job.status == JobStatus.COMPLETED, Job.started_at.isnot(None), Job.completed_at.isnot(None))
            .order_by(Job.completed_at.desc())
            .limit(50)
            .all()
        )
        if not completed:
            return None
        durations = [(j.completed_at - j.started_at).total_seconds() for j in completed]
        return sum(durations) / len(durations)