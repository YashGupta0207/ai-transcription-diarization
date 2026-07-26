"""
Shared constants/enums that both the desktop client and backend agree on
(e.g. job status strings), so the desktop app never has to guess string
values by hand. Kept dependency-free (no SQLAlchemy) so the desktop app
doesn't need backend packages installed.
"""

JOB_STATUSES = [
    "uploading", "queued", "processing", "completed", "failed", "cancelled", "retrying",
]

SUPPORTED_EXTENSIONS = (
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".mov", ".mkv", ".avi",
)

EXPORT_FORMATS = ["txt", "json", "srt", "vtt"]
