"""Storage backend interface. All backends implement save/load/delete/url."""
from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageBackend(ABC):
    @abstractmethod
    def save(self, key: str, file_obj: BinaryIO) -> str:
        """Persist file, return the storage key actually used."""

    @abstractmethod
    def get_local_path(self, key: str) -> str:
        """Return a local filesystem path the worker can read from (downloads if remote)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a (possibly signed/temporary) URL for the object."""
