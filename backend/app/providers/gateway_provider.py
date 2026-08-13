"""
AI Gateway provider.

Uses the AI Gateway Platform SDK (dxdeepgram) to route transcription requests
through the gateway.
"""
import sys
import os

from app.ai_gateway_sdk.dxai import DXAI
from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment, WordTimestamp
from app.config import settings


def _speaker_label(value) -> str:
    """Normalize Deepgram's optional numeric speaker value safely."""
    try:
        return f"Speaker {int(value) + 1}"
    except (TypeError, ValueError):
        return "Speaker 1"


class GatewayProvider(SpeechProvider):
    def __init__(self, api_key: str = None, base_url: str = None, target_provider: str = None):
        self.api_key = api_key or settings.GATEWAY_API_KEY
        self.base_url = base_url or settings.GATEWAY_BASE_URL
        self.target_provider = target_provider or settings.GATEWAY_TARGET_PROVIDER
        if not self.api_key:
            raise ValueError("GATEWAY_API_KEY is not configured")
            
        self.client = DXAI(api_key=self.api_key, base_url=self.base_url)

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en") -> TranscriptionResult:
        with open(audio_file_path, "rb") as f:
            audio_bytes = f.read()
            
        target_lower = self.target_provider.lower()
        if "azureopenai" in target_lower or "openai" in target_lower or "whisper" in target_lower:
            data = self.client.transcribe(
                provider=self.target_provider,
                path="/audio/transcriptions",
                files={"file": (os.path.basename(audio_file_path), audio_bytes, __import__("mimetypes").guess_type(audio_file_path)[0] or "audio/wav")},
                data={"model": "whisper-1", "response_format": "verbose_json", "language": language},
            )
            
            # Step 2: Diarization via Chat Completion
            full_text = data.get("text", "")
            if not full_text:
                full_text = " ".join([seg.get("text", "") for seg in data.get("segments", [])])
                
            import json
            prompt = (
                "Split the following transcript into speaker-labeled segments "
                "with estimated start/end seconds if inferable. Return strict JSON: "
                '{"segments":[{"speaker":"Speaker 1","start":0.0,"end":0.0,"text":"..."}]}. '
                f"Transcript:\n{full_text}"
            )
            
            segments = []
            try:
                chat_resp = self.client.chat(
                    provider=self.target_provider,
                    model="gpt-4o",
                    prompt=prompt,
                    response_format={"type": "json_object"}
                )
                content = chat_resp["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                
                for s in parsed.get("segments", []):
                    segments.append(TranscriptSegment(
                        speaker=s.get("speaker", "Speaker 1"),
                        start=float(s.get("start", 0.0)),
                        end=float(s.get("end", 0.0)),
                        text=s.get("text", "").strip(),
                        confidence=None,
                    ))
            except Exception as e:
                print(f"Gateway diarization failed: {e}")
                # Fallback to single speaker
                for seg in data.get("segments", []):
                    segments.append(TranscriptSegment(
                        speaker="Speaker 1",
                        start=seg.get("start", 0.0),
                        end=seg.get("end", 0.0),
                        text=seg.get("text", "").strip(),
                        confidence=None,
                    ))
                    
            words = []
            return TranscriptionResult(segments=segments, raw_response=data, language=language, words=words)

        elif "azure" in target_lower:
            import json
            definition = {
                "locales": [language],
                "profanityFilterMode": "Masked",
                "diarization": {
                    "speakers": {
                        "minCount": 1,
                        "maxCount": 5
                    }
                }
            }
            data = self.client.transcribe(
                provider=self.target_provider,
                path="/speechtotext/transcriptions:transcribe?api-version=2024-11-15",
                files={"audio": (os.path.basename(audio_file_path), audio_bytes, "audio/wav")},
                data={"definition": json.dumps(definition)},
            )
            
            segments = []
            words = []
            phrases = data.get("recognizedPhrases", [])
            for phrase in phrases:
                if phrase.get("recognitionStatus") != "Success":
                    continue
                speaker_label = _speaker_label(phrase.get("speaker", 0))
                
                def parse_pt(pt_str):
                    if not pt_str: return 0.0
                    pt_str = pt_str.replace("PT", "")
                    minutes = 0.0
                    if "M" in pt_str:
                        m_part, pt_str = pt_str.split("M")
                        minutes = float(m_part)
                    seconds = 0.0
                    if "S" in pt_str:
                        seconds = float(pt_str.replace("S", ""))
                    return minutes * 60 + seconds
                
                start = parse_pt(phrase.get("offset"))
                duration = parse_pt(phrase.get("duration"))
                
                best = phrase.get("nBest", [{}])[0]
                text = best.get("display", "")
                confidence = best.get("confidence", 0.0)
                
                segments.append(TranscriptSegment(
                    speaker=speaker_label,
                    start=start,
                    end=start + duration,
                    text=text,
                    confidence=confidence,
                ))
                
                for w in best.get("words", []):
                    w_start = parse_pt(w.get("offset"))
                    w_duration = parse_pt(w.get("duration"))
                    words.append(WordTimestamp(
                        speaker=speaker_label,
                        word=w.get("word", ""),
                        start=w_start,
                        end=w_start + w_duration,
                        confidence=w.get("confidence", 0.0)
                    ))
            words.sort(key=lambda word: word.start)
            return TranscriptionResult(segments=segments, raw_response=data, language=language, words=words)
            
        elif "deepgram" in target_lower or "gateway" in target_lower:
            params = {
                "model": "nova-2",
                "diarize": "true",
                "punctuate": "true",
                "utterances": "true",
                "language": language,
            }
            
            data = self.client.transcribe(
                provider=self.target_provider,
                audio=audio_bytes,
                mimetype="audio/wav",
                params=params,
            )

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
            
        elif "assemblyai" in target_lower or "gladia" in target_lower:
            raise NotImplementedError(
                f"Gateway routing for {self.target_provider} is not supported because it requires a two-step upload and poll process."
            )
        else:
            raise ValueError(f"Unsupported target provider for gateway: {self.target_provider}")
