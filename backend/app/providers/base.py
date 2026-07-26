"""
SpeechProvider interface.
Every AI transcription/diarization vendor implements this exact contract, so the
rest of the app (worker, API, desktop) never needs to know which vendor is active.

To add a new provider:
  1. Create providers/<name>_provider.py implementing SpeechProvider
  2. Register it in providers/factory.py
  3. Set SPEECH_PROVIDER=<name> in .env

Nothing else in the codebase changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TranscriptSegment:
    speaker: str
    start: float
    end: float
    text: str
    confidence: Optional[float] = None


@dataclass
class TranscriptionResult:
    segments: List[TranscriptSegment]
    raw_response: dict
    language: str = "en"


class SpeechProvider(ABC):
    """Unified interface for transcription + speaker diarization."""

    @abstractmethod
    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        """
        Run transcription + diarization on a local audio file path and return
        a normalized TranscriptionResult, regardless of vendor response shape.
        """
        raise NotImplementedError
