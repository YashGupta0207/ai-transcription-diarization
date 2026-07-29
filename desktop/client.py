"""
Thin HTTP/WebSocket client wrapping the backend REST + live API.
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
        self.api_key = api_key or DESKTOP_API_KEY
        self.headers = {"X-API-Key": self.api_key}

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

    def get_words(self, job_id: str) -> list:
        """Word-level timestamps for the Playback Verification feature.
        Returns an empty list for jobs without word timing - the caller
        handles that gracefully rather than crashing."""
        resp = requests.get(f"{self.base_url}/jobs/{job_id}/words", headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def download_audio(self, job_id: str, dest_path: str) -> str:
        """
        Downloads the job's original audio to a local temp file so QMediaPlayer
        can play it. We can't point QMediaPlayer directly at the backend URL
        because that endpoint requires an X-API-Key header, which QMediaPlayer
        has no way to attach to its network requests.
        """
        # Streaming avoids keeping an entire multi-hour recording in memory.
        with requests.get(
            f"{self.base_url}/jobs/{job_id}/audio", headers=self.headers, timeout=120, stream=True
        ) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return dest_path

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

    def live_ws_url(self) -> str:
        """Builds the ws:// or wss:// URL for the live transcription endpoint,
        derived from the same base_url used for everything else."""
        if self.base_url.startswith("https://"):
            return self.base_url.replace("https://", "wss://") + "/ws/live?format=linear16"
        return self.base_url.replace("http://", "ws://") + "/ws/live?format=linear16"
