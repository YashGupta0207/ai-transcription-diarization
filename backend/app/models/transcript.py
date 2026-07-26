"""
Placeholder module kept for clean separation if transcript-specific models
(e.g. versioned edits, export history) are added later.
Currently Segment/Speaker in job.py cover transcript storage needs.
"""
from app.models.job import Segment, Speaker  # noqa: F401
