"""
OpenAI Whisper provider - FALLBACK #1.

Note: OpenAI's hosted Whisper API does NOT provide speaker diarization natively.
We approximate diarization here by treating each returned segment as a single
speaker ("Speaker 1") unless a separate diarization pass (e.g. pyannote) is
wired in on the worker side. This keeps the provider interface honest about
its limitations rather than faking multi-speaker output.
"""
import requests

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment
from app.config import settings


class WhisperProvider(SpeechProvider):
    API_URL = "https://api.openai.com/v1/audio/transcriptions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with open(audio_file_path, "rb") as f:
            files = {"file": f}
            data = {
                "model": "whisper-1",
                "response_format": "verbose_json",
                "language": language,
            }
            resp = requests.post(self.API_URL, headers=headers, files=files, data=data, timeout=600)
        resp.raise_for_status()
        payload = resp.json()

        segments = []
        for seg in payload.get("segments", []):
            segments.append(
                TranscriptSegment(
                    speaker="Speaker 1",  # no native diarization
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    confidence=None,
                )
            )

        return TranscriptionResult(segments=segments, raw_response=payload, language=language)
