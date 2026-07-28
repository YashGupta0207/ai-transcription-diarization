"""
Thin HTTP client wrapping the backend REST API.
The desktop app NEVER talks to Deepgram/OpenAI/etc directly and NEVER holds
provider API keys - only the DESKTOP_API_KEY shared secret used to
authenticate with our own backend.
"""
import os
import requests

# Hardcoded so the packaged .exe works standalone for any user, with no
# environment variables required. Update these two values if the backend
# URL or API key ever changes, then rebuild the .exe.
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "https://ai-transcription-diarization.onrender.com")
DESKTOP_API_KEY = os.environ.get("DESKTOP_API_KEY", "dev-desktop-key-change-me")


class BackendClient:
    def __init__(self, base_url: str = None, api_key: str = None):
        self.base_url = (base_url or BACKEND_BASE_URL).rstrip("/")
        self.headers = {"X-API-Key": api_key or DESKTOP_API_KEY}

    def upload_file(self, file_path: str, provider: str = None) -> dict:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f)}
            data = {"provider": provider} if provider else {}
            resp = requests.post(
                f"{self.base_url}/upload", headers=self.headers, files=files, data=data, timeout=120
            )
        resp.raise_for_status()
        return resp.json()

    def get_status(self, job_id: str) -> dict:
        resp = requests.get(f"{self.base_url}/jobs/{job_id}", headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_jobs(self, limit: int = 100) -> dict:
        resp = requests.get(f"{self.base_url}/jobs", headers=self.headers, params={"limit": limit}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_result(self, job_id: str) -> dict:
        resp = requests.get(f"{self.base_url}/jobs/{job_id}/result", headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def export(self, job_id: str, fmt: str) -> str:
        resp = requests.get(
            f"{self.base_url}/jobs/{job_id}/export", headers=self.headers, params={"format": fmt}, timeout=30
        )
        resp.raise_for_status()
        return resp.text

    def cancel(self, job_id: str) -> dict:
        resp = requests.post(f"{self.base_url}/jobs/{job_id}/cancel", headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def delete(self, job_id: str) -> dict:
        resp = requests.delete(f"{self.base_url}/jobs/{job_id}", headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict:
        resp = requests.get(f"{self.base_url}/health", timeout=15)
        resp.raise_for_status()
        return resp.json()