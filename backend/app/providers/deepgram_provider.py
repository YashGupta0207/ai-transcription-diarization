"""
Deepgram provider - PRIMARY choice.

Why Deepgram is the default:
  - Native diarization + word-level timestamps + confidence in one call (no
    separate diarization step / library needed, unlike raw Whisper).
  - Fast (streaming-capable, but here we use prerecorded REST API which is
    simpler and sufficient for async background jobs).
  - Generous free trial credits, pay-as-you-go beyond that.
"""
import requests

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment, WordTimestamp
from app.config import settings


class DeepgramProvider(SpeechProvider):
    API_URL = "https://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.DEEPGRAM_API_KEY
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY is not configured")

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        params = {
            "model": "nova-2",
            "diarize": "true",
            "punctuate": "true",
            "utterances": "true",
            "language": language,
        }
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/*",
        }
        with open(audio_file_path, "rb") as f:
            resp = requests.post(self.API_URL, params=params, headers=headers, data=f, timeout=600)
        resp.raise_for_status()
        data = resp.json()

        segments = []
        words = []
        utterances = data.get("results", {}).get("utterances", [])
        for i, utt in enumerate(utterances):
            speaker_idx = utt.get("speaker", 0)
            speaker_label = f"Speaker {speaker_idx + 1}"
            segments.append(
                TranscriptSegment(
                    speaker=speaker_label,
                    start=utt.get("start", 0.0),
                    end=utt.get("end", 0.0),
                    text=utt.get("transcript", "").strip(),
                    confidence=utt.get("confidence"),
                )
            )
            # Word-level timing, used by the Playback Verification feature.
            # Additive only - existing transcript/segment behavior above is
            # completely unchanged.
            for w in utt.get("words", []):
                words.append(
                    WordTimestamp(
                        speaker=speaker_label,
                        word=(w.get("punctuated_word") or w.get("word", "")),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        confidence=w.get("confidence"),
                    )
                )

        return TranscriptionResult(segments=segments, raw_response=data, language=language, words=words)