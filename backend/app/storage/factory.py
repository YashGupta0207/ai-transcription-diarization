"""Returns the configured storage backend. Single switch point for the whole app."""
from app.config import settings
from app.storage.base import StorageBackend
from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage


def get_storage() -> StorageBackend:
    backend = settings.STORAGE_BACKEND.lower()
    if backend in ("s3", "b2"):
        return S3Storage()
    return LocalStorage()
