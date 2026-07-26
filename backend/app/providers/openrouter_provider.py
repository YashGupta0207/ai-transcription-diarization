"""
OpenRouter provider - FALLBACK #2.

OpenRouter itself doesn't host audio transcription models directly in the
same way as Deepgram/Whisper; in practice this provider is best used for
POST-PROCESSING a raw transcript (e.g. cleanup, punctuation restoration,
speaker-label normalization) using an LLM, after a cheap local/other ASR
pass. Included here to satisfy the provider-abstraction requirement and as
a scaffold - swap the `_raw_transcribe` step for any ASR you have local
access to (e.g. faster-whisper) and let OpenRouter refine speaker labels.
"""
import json
import requests

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment
from app.config import settings


class OpenRouterProvider(SpeechProvider):
    CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")

    def _raw_transcribe(self, audio_file_path: str) -> str:
        """
        Placeholder for a local/cheap ASR pass (e.g. faster-whisper running
        on the worker). Replace with a real call; raises here to make the
        limitation explicit rather than silently no-op.
        """
        raise NotImplementedError(
            "OpenRouterProvider requires a raw ASR step (e.g. faster-whisper) "
            "to produce a transcript before LLM post-processing can run."
        )

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        raw_text = self._raw_transcribe(audio_file_path)

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        prompt = (
            "Split the following transcript into speaker-labeled segments "
            "with estimated start/end seconds if inferable. Return strict JSON: "
            '{"segments":[{"speaker":"Speaker 1","start":0.0,"end":0.0,"text":"..."}]}. '
            f"Transcript:\n{raw_text}"
        )
        body = {
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = requests.post(self.CHAT_URL, headers=headers, data=json.dumps(body), timeout=300)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        segments = [
            TranscriptSegment(
                speaker=s.get("speaker", "Speaker 1"),
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=s.get("text", "").strip(),
            )
            for s in parsed.get("segments", [])
        ]
        return TranscriptionResult(segments=segments, raw_response=parsed, language=language)
