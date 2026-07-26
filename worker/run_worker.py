"""
Standalone entrypoint to start an RQ worker process.
Run with: python worker/run_worker.py
(Used as the Render 'Background Worker' service start command.)
"""
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..")
sys.path.insert(0, os.path.join(_THIS_DIR, "..", "backend"))
sys.path.insert(0, _PROJECT_ROOT)

from redis import Redis
from rq import Worker, Queue
from app.config import settings
from app.utils.logging_config import setup_logging

logger = setup_logging("worker")

if __name__ == "__main__":
    conn = Redis.from_url(settings.REDIS_URL)
    queue = Queue(settings.QUEUE_NAME, connection=conn)
    logger.info(f"Starting RQ worker, listening on queue '{settings.QUEUE_NAME}'")
    worker = Worker([queue], connection=conn)
    worker.work(with_scheduler=True)
