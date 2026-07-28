"""Pydantic request/response models for the API layer."""
from typing import Optional, List
from pydantic import BaseModel


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class SegmentOut(BaseModel):
    speaker: str
    start: float
    end: float
    text: str
    confidence: Optional[float] = None

    class Config:
        from_attributes = True


class WordOut(BaseModel):
    """Used only by the Playback Verification feature (GET /jobs/{id}/words).
    Does not touch JobResultResponse below, so existing clients calling
    /jobs/{id}/result are completely unaffected."""
    speaker: str
    word: str
    start: float
    end: float
    confidence: Optional[float] = None

    class Config:
        from_attributes = True


class JobStatusResponse(BaseModel):
    id: str
    status: str
    original_filename: str
    provider: str
    error_message: Optional[str] = None
    retry_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class JobResultResponse(BaseModel):
    id: str
    status: str
    readable_transcript: Optional[str] = None
    segments: List[SegmentOut] = []


class JobListResponse(BaseModel):
    jobs: List[JobStatusResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    queue_length: int
    failed_jobs: int
    avg_processing_time_seconds: Optional[float] = None