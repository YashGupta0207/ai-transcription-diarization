"""
AI Gateway provider.

Uses the AI Gateway Platform SDK (dxdeepgram) to route transcription requests
through the gateway.
"""
import sys
import os

# Add the SDK to the python path
sdk_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../ai_gateway_sdk"))
if sdk_path not in sys.path:
    sys.path.insert(0, sdk_path)

from dxdeepgram import DeepgramClient
from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment, WordTimestamp
from app.config import settings


def _speaker_label(value) -> str:
    """Normalize Deepgram's optional numeric speaker value safely."""
    try:
        return f"Speaker {int(value) + 1}"
    except (TypeError, ValueError):
        return "Speaker 1"


class GatewayProvider(SpeechProvider):
    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or settings.GATEWAY_API_KEY
        self.base_url = base_url or settings.GATEWAY_BASE_URL
        if not self.api_key:
            raise ValueError("GATEWAY_API_KEY is not configured")
            
        self.client = DeepgramClient(api_key=self.api_key, base_url=self.base_url)

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        # The gateway uses Deepgram under the hood, so we pass Deepgram parameters
        params = {
            "model": "nova-2",
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
            "language": language,
        }
        
        data = self.client.transcribe_file(audio_file_path, mimetype="audio/wav", **params)

        segments = []
        words = []
        results = data.get("results", {})
        utterances = results.get("utterances", [])
        for i, utt in enumerate(utterances):
            speaker_idx = utt.get("speaker", 0)
            speaker_label = _speaker_label(speaker_idx)
            segments.append(
                TranscriptSegment(
                    speaker=speaker_label,
                    start=utt.get("start", 0.0),
                    end=utt.get("end", 0.0),
                    text=utt.get("transcript", "").strip(),
                    confidence=utt.get("confidence"),
                )
            )
            for w in utt.get("words", []):
                if not w.get("word") or w.get("start") is None or w.get("end") is None:
                    continue
                words.append(
                    WordTimestamp(
                        speaker=speaker_label,
                        word=(w.get("punctuated_word") or w.get("word", "")),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        confidence=w.get("confidence"),
                    )
                )

        if not words:
            alternative = ((results.get("channels") or [{}])[0].get("alternatives") or [{}])[0]
            for w in alternative.get("words", []):
                if not w.get("word") or w.get("start") is None or w.get("end") is None:
                    continue
                speaker_label = _speaker_label(w.get("speaker", 0))
                words.append(WordTimestamp(speaker=speaker_label, word=w.get("punctuated_word") or w["word"],
                                           start=w["start"], end=w["end"], confidence=w.get("confidence")))
        words.sort(key=lambda word: word.start)
        return TranscriptionResult(segments=segments, raw_response=data, language=language, words=words)
