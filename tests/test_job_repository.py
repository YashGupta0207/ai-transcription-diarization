"""Integration tests for JobRepository against an in-memory SQLite DB."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.repositories.job_repository import JobRepository
from app.models.job import JobStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_job_defaults_to_uploading(db_session):
    repo = JobRepository(db_session)
    job = repo.create_job("test.mp3", "gateway")
    assert job.status == JobStatus.UPLOADING
    assert job.original_filename == "test.mp3"


def test_status_transitions_set_timestamps(db_session):
    repo = JobRepository(db_session)
    job = repo.create_job("test.mp3", "gateway")

    job = repo.set_status(job, JobStatus.QUEUED)
    assert job.queued_at is not None

    job = repo.set_status(job, JobStatus.PROCESSING)
    assert job.started_at is not None

    job = repo.set_status(job, JobStatus.COMPLETED)
    assert job.completed_at is not None


def test_save_results_creates_segments_and_speakers(db_session):
    repo = JobRepository(db_session)
    job = repo.create_job("test.mp3", "gateway")

    segments = [
        {"speaker": "Speaker 1", "start": 0.0, "end": 1.0, "text": "Hello", "confidence": 0.98},
        {"speaker": "Speaker 2", "start": 1.0, "end": 2.0, "text": "Hi", "confidence": 0.95},
    ]
    repo.save_results(job, segments, "Speaker 1\n\nHello\n\nSpeaker 2\n\nHi", {"raw": True})

    stored = repo.get_segments(job.id)
    assert len(stored) == 2
    assert stored[0].text == "Hello"
    assert job.readable_transcript.startswith("Speaker 1")


def test_save_results_persists_word_timestamps_in_order(db_session):
    repo = JobRepository(db_session)
    job = repo.create_job("test.mp3", "gateway")
    repo.save_results(job, [], "", {}, words=[
        {"speaker": "Speaker 1", "word": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.98},
        {"speaker": "Speaker 1", "word": "there", "start": 0.5, "end": 0.9, "confidence": 0.97},
    ])
    words = repo.get_words(job.id)
    assert [(w.word, w.start, w.end) for w in words] == [("Hello", 0.0, 0.4), ("there", 0.5, 0.9)]


def test_increment_retry(db_session):
    repo = JobRepository(db_session)
    job = repo.create_job("test.mp3", "gateway")
    assert job.retry_count == 0
    job = repo.increment_retry(job)
    assert job.retry_count == 1
