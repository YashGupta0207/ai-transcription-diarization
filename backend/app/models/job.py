"""
Core database models: Job, File, Speaker, Segment, TranscriptWord.
Transcript text is denormalized into Segment rows for query flexibility,
and a convenience `full_text` / `raw_provider_json` is kept on Job for fast retrieval.

TranscriptWord is an additive table (does not alter any existing table) used
by the Playback Verification feature for word-level highlight synchronization.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Enum, ForeignKey, Float, Integer, Text, JSON
)
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class JobStatus(str, enum.Enum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    status = Column(Enum(JobStatus), default=JobStatus.UPLOADING, nullable=False, index=True)

    original_filename = Column(String, nullable=False)
    file_id = Column(String, ForeignKey("files.id"), nullable=True)

    provider = Column(String, default="deepgram")
    language = Column(String, default="en")

    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # Denormalized readable transcript ("Speaker 1\nHello...\n\nSpeaker 2\n...")
    readable_transcript = Column(Text, nullable=True)
    raw_provider_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    queued_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    file = relationship("MediaFile", back_populates="job", uselist=False)
    segments = relationship("Segment", back_populates="job", cascade="all, delete-orphan")
    speakers = relationship("Speaker", back_populates="job", cascade="all, delete-orphan")
    words = relationship("TranscriptWord", back_populates="job", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status.value if isinstance(self.status, JobStatus) else self.status,
            "original_filename": self.original_filename,
            "provider": self.provider,
            "language": self.language,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class MediaFile(Base):
    __tablename__ = "files"

    id = Column(String, primary_key=True, default=gen_uuid)
    storage_key = Column(String, nullable=False)      # path/key in storage backend
    storage_backend = Column(String, default="local")
    content_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="file", uselist=False)


class Speaker(Base):
    __tablename__ = "speakers"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    label = Column(String, nullable=False)   # "Speaker 1", "Speaker 2" ...
    display_name = Column(String, nullable=True)  # user-editable friendly name

    job = relationship("Job", back_populates="speakers")


class Segment(Base):
    __tablename__ = "segments"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    speaker_label = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    start = Column(Float, nullable=False)
    end = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)

    job = relationship("Job", back_populates="segments")


class TranscriptWord(Base):
    """
    Word-level timestamps, additive table used only by the Playback
    Verification feature (highlight-as-you-play, click-to-seek). Does not
    affect Segment/Speaker/Job in any way - existing transcript rendering
    and exports are completely unaffected by this table's existence.
    """
    __tablename__ = "transcript_words"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    speaker_label = Column(String, nullable=False)
    word = Column(String, nullable=False)
    start = Column(Float, nullable=False)
    end = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)

    job = relationship("Job", back_populates="words")