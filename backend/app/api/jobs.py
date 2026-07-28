"""Job-related REST endpoints: upload, start, status, list, result, cancel/delete."""
import os

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import verify_api_key
from app.services.job_service import JobService
from app.schemas.job import (
    JobCreateResponse, JobStatusResponse, JobResultResponse, JobListResponse, SegmentOut, WordOut
)
from app.utils.media import segments_to_srt, segments_to_vtt, segments_to_readable
from app.storage.factory import get_storage

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(verify_api_key)])
upload_router = APIRouter(tags=["upload"], dependencies=[Depends(verify_api_key)])


@upload_router.post("/upload", response_model=JobCreateResponse)
async def upload_and_start(
    file: UploadFile = File(...),
    provider: str = Form(None),
    db: Session = Depends(get_db),
):
    """
    Combines the 'upload' + 'start job' steps into a single call for simplicity.
    (A separate POST /jobs/start also exists below for clients that want to
    decouple upload completion from processing start, e.g. resumable uploads.)
    """
    service = JobService(db)
    try:
        job = service.create_job_from_upload(file.filename, file.file, file.content_type, provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JobCreateResponse(job_id=job.id, status=job.status.value)


@router.post("/start/{job_id}", response_model=JobCreateResponse)
def start_existing_job(job_id: str, db: Session = Depends(get_db)):
    """Re-enqueue an existing job (e.g. manual retry after a permanent failure)."""
    from app.services.job_service import job_queue
    from app.models.job import JobStatus

    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    service.repo.set_status(job, JobStatus.QUEUED)
    job_queue.enqueue("worker.tasks.process_transcription_job", job.id, job_timeout="30m")
    return JobCreateResponse(job_id=job.id, status=job.status.value)


@router.get("", response_model=JobListResponse)
def list_jobs(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    service = JobService(db)
    jobs, total = service.list_jobs(limit, offset)
    return JobListResponse(jobs=[JobStatusResponse(**j.to_dict()) for j in jobs], total=total)


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(**job.to_dict())


@router.get("/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str, db: Session = Depends(get_db)):
    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    segments = service.repo.get_segments(job_id)
    return JobResultResponse(
        id=job.id,
        status=job.status.value,
        readable_transcript=job.readable_transcript,
        segments=[
            SegmentOut(speaker=s.speaker_label, start=s.start, end=s.end, text=s.text, confidence=s.confidence)
            for s in segments
        ],
    )


@router.get("/{job_id}/words", response_model=list[WordOut])
def get_job_words(job_id: str, db: Session = Depends(get_db)):
    """
    Word-level timestamps for the Playback Verification feature. Returns an
    empty list for jobs processed before this feature existed, or for any
    provider that didn't supply word-level timing - the playback UI handles
    that gracefully by showing a message instead of crashing.
    """
    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    words = service.repo.get_words(job_id)
    return [
        WordOut(speaker=w.speaker_label, word=w.word, start=w.start, end=w.end, confidence=w.confidence)
        for w in words
    ]


@router.get("/{job_id}/audio")
def get_job_audio(job_id: str, db: Session = Depends(get_db)):
    """
    Redirects to a playable URL for the job's original audio/video file, used
    by the Playback Verification feature's <audio> element. Works transparently
    whether storage is local (redirects to the /files static mount) or S3/B2
    (redirects to a signed temporary URL) - the frontend never needs to know
    which storage backend is active.
    """
    service = JobService(db)
    job = service.get_job(job_id)
    if not job or not job.file:
        raise HTTPException(status_code=404, detail="Job or its audio file not found")

    storage = get_storage()
    url = storage.get_url(job.file.storage_key)
    return RedirectResponse(url)


@router.get("/{job_id}/playback-url")
def get_job_playback_url(job_id: str, db: Session = Depends(get_db)):
    """Return a browser-playable URL after authenticating the API request.

    Native HTML media elements cannot add the desktop API-key header to their
    own requests.  This additive endpoint lets the web UI authenticate once,
    then receive either a same-origin local-media route or a short-lived S3/B2
    URL that supports browser streaming and range seeking.
    """
    service = JobService(db)
    job = service.get_job(job_id)
    if not job or not job.file:
        raise HTTPException(status_code=404, detail="Job or its audio file not found")
    return {"url": get_storage().get_url(job.file.storage_key)}


@router.get("/{job_id}/export")
def export_job(job_id: str, format: str = "txt", db: Session = Depends(get_db)):
    """Export formats: txt | json | srt | vtt"""
    service = JobService(db)
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    segments = service.repo.get_segments(job_id)
    seg_dicts = [
        {"speaker": s.speaker_label, "start": s.start, "end": s.end, "text": s.text, "confidence": s.confidence}
        for s in segments
    ]

    fmt = format.lower()
    if fmt == "txt":
        return PlainTextResponse(job.readable_transcript or segments_to_readable(seg_dicts))
    if fmt == "json":
        return JSONResponse({"segments": seg_dicts})
    if fmt == "srt":
        return PlainTextResponse(segments_to_srt(seg_dicts), media_type="text/plain")
    if fmt == "vtt":
        return PlainTextResponse(segments_to_vtt(seg_dicts), media_type="text/vtt")

    raise HTTPException(status_code=400, detail="format must be one of: txt, json, srt, vtt")


@router.delete("/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    service = JobService(db)
    ok = service.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"deleted": True, "job_id": job_id}


@router.post("/{job_id}/cancel", response_model=JobStatusResponse)
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    service = JobService(db)
    job = service.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(**job.to_dict())
