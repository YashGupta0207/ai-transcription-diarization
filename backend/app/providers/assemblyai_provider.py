"""AssemblyAI provider - FALLBACK #3. Native diarization + polling-based API."""
import time
import requests

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment
from app.config import settings


class AssemblyAIProvider(SpeechProvider):
    BASE_URL = "https://api.assemblyai.com/v2"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.ASSEMBLYAI_API_KEY
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is not configured")
        self.headers = {"authorization": self.api_key}

    def _upload(self, audio_file_path: str) -> str:
        with open(audio_file_path, "rb") as f:
            resp = requests.post(f"{self.BASE_URL}/upload", headers=self.headers, data=f, timeout=600)
        resp.raise_for_status()
        return resp.json()["upload_url"]

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        audio_url = self._upload(audio_file_path)

        resp = requests.post(
            f"{self.BASE_URL}/transcript",
            headers=self.headers,
            json={"audio_url": audio_url, "speaker_labels": True, "language_code": language},
            timeout=60,
        )
        resp.raise_for_status()
        transcript_id = resp.json()["id"]

        # Poll until complete (worker context, so blocking sleep is fine)
        while True:
            poll = requests.get(f"{self.BASE_URL}/transcript/{transcript_id}", headers=self.headers, timeout=60)
            poll.raise_for_status()
            data = poll.json()
            if data["status"] == "completed":
                break
            if data["status"] == "error":
                raise RuntimeError(f"AssemblyAI error: {data.get('error')}")
            time.sleep(5)

        segments = []
        for utt in data.get("utterances", []):
            segments.append(
                TranscriptSegment(
                    speaker=f"Speaker {utt.get('speaker', '1')}",
                    start=utt.get("start", 0) / 1000.0,
                    end=utt.get("end", 0) / 1000.0,
                    text=utt.get("text", "").strip(),
                    confidence=utt.get("confidence"),
                )
            )
        return TranscriptionResult(segments=segments, raw_response=data, language=language)
