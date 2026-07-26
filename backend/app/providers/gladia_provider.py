"""Gladia provider - FALLBACK #4. Native diarization via async transcription API."""
import time
import requests

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment
from app.config import settings


class GladiaProvider(SpeechProvider):
    BASE_URL = "https://api.gladia.io/v2"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.GLADIA_API_KEY
        if not self.api_key:
            raise ValueError("GLADIA_API_KEY is not configured")
        self.headers = {"x-gladia-key": self.api_key}

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        with open(audio_file_path, "rb") as f:
            files = {"audio": f}
            upload = requests.post(f"{self.BASE_URL}/upload", headers=self.headers, files=files, timeout=600)
        upload.raise_for_status()
        audio_url = upload.json()["audio_url"]

        resp = requests.post(
            f"{self.BASE_URL}/transcription",
            headers=self.headers,
            json={"audio_url": audio_url, "diarization": True, "language": language},
            timeout=60,
        )
        resp.raise_for_status()
        result_url = resp.json()["result_url"]

        while True:
            poll = requests.get(result_url, headers=self.headers, timeout=60)
            poll.raise_for_status()
            data = poll.json()
            if data["status"] == "done":
                break
            if data["status"] == "error":
                raise RuntimeError(f"Gladia error: {data}")
            time.sleep(5)

        segments = []
        utterances = data.get("result", {}).get("transcription", {}).get("utterances", [])
        for utt in utterances:
            segments.append(
                TranscriptSegment(
                    speaker=f"Speaker {utt.get('speaker', 0) + 1}",
                    start=utt.get("start", 0.0),
                    end=utt.get("end", 0.0),
                    text=utt.get("text", "").strip(),
                    confidence=utt.get("confidence"),
                )
            )
        return TranscriptionResult(segments=segments, raw_response=data, language=language)
