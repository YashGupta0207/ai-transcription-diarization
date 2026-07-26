"""Health / monitoring endpoint - no auth required so uptime monitors (e.g. Render, UptimeRobot) can hit it freely."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.job_service import JobService
from app.schemas.job import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    service = JobService(db)
    return HealthResponse(
        status="ok",
        queue_length=service.queue_length(),
        failed_jobs=service.failed_count(),
        avg_processing_time_seconds=service.avg_processing_time(),
    )
