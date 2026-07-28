"""Local filesystem storage backend - used for development."""
import os
import shutil
from typing import BinaryIO

from app.config import settings
from app.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or settings.LOCAL_STORAGE_DIR
        os.makedirs(self.base_dir, exist_ok=True)

    def _full_path(self, key: str) -> str:
        return os.path.join(self.base_dir, key)

    def save(self, key: str, file_obj: BinaryIO) -> str:
        full_path = self._full_path(key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as out:
            shutil.copyfileobj(file_obj, out)
        return key

    def get_local_path(self, key: str) -> str:
        return self._full_path(key)

    def delete(self, key: str) -> None:
        path = self._full_path(key)
        if os.path.exists(path):
            os.remove(path)

    def get_url(self, key: str, expires_in: int = 3600) -> str:
        # A relative route keeps redirects and browser playback on the same
        # host in local Docker and Render deployments alike.
        return f"/files/{key}"
