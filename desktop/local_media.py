"""Desktop-local media paths stored beside the application installation."""
import json
import sys
from pathlib import Path


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def transcription_audio_directory() -> Path:
    directory = application_directory() / "transcription_audio"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class LocalMediaRegistry:
    """Persist job-id to original-local-file-path mappings next to the app."""
    def __init__(self):
        self.path = transcription_audio_directory() / "media_index.json"

    def get(self, job_id: str) -> str:
        return str(self._read().get(job_id, ""))

    def set(self, job_id: str, media_path: str):
        data = self._read()
        data[job_id] = str(Path(media_path).resolve())
        self._write(data)

    def remove(self, job_id: str):
        data = self._read()
        if job_id in data:
            del data[job_id]
            self._write(data)

    def _read(self) -> dict:
        try:
            with self.path.open("r", encoding="utf-8") as source:
                data = json.load(source)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
        temporary.replace(self.path)
