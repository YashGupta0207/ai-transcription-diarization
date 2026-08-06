"""
Azure Speech provider.

Uses Azure Cognitive Services Speech SDK for transcription and diarization.
"""
import azure.cognitiveservices.speech as speechsdk

from app.providers.base import SpeechProvider, TranscriptionResult, TranscriptSegment, WordTimestamp
from app.config import settings


class AzureProvider(SpeechProvider):
    def __init__(self, api_key: str = None, region: str = None):
        self.api_key = api_key or settings.AZURE_SPEECH_KEY
        self.region = region or settings.AZURE_SPEECH_REGION
        if not self.api_key or not self.region:
            raise ValueError("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must be configured")

    def transcribe_and_diarize(self, audio_file_path: str, language: str = "en-US") -> TranscriptionResult:
        speech_config = speechsdk.SpeechConfig(subscription=self.api_key, region=self.region)
        speech_config.speech_recognition_language = language
        
        # Enable detailed output to get word-level timestamps and confidence
        speech_config.output_format = speechsdk.OutputFormat.Detailed
        speech_config.request_word_level_timestamps()

        audio_config = speechsdk.audio.AudioConfig(filename=audio_file_path)
        
        # We use ConversationTranscriber for diarization
        transcriber = speechsdk.transcription.ConversationTranscriber(speech_config=speech_config, audio_config=audio_config)
        
        segments = []
        words = []
        
        done = False
        
        def session_stopped_cb(evt):
            nonlocal done
            done = True
            
        def canceled_cb(evt):
            nonlocal done
            done = True
            
        def transcribed_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                speaker_id = evt.result.speaker_id
                speaker_label = f"Speaker {speaker_id}" if speaker_id else "Speaker 1"
                
                # Convert ticks to seconds (1 tick = 100 nanoseconds)
                start_sec = evt.result.offset / 10000000.0
                duration_sec = evt.result.duration / 10000000.0
                end_sec = start_sec + duration_sec
                
                text = evt.result.text
                
                # Get detailed results for word-level timestamps and confidence
                # The detailed result is available in JSON format
                import json
                properties = evt.result.properties
                json_result = properties.get(speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
                confidence = 0.0
                
                if json_result:
                    try:
                        parsed = json.loads(json_result)
                        n_best = parsed.get("NBest", [])
                        if n_best:
                            best = n_best[0]
                            confidence = best.get("Confidence", 0.0)
                            
                            for word_info in best.get("Words", []):
                                w_text = word_info.get("Word", "")
                                w_offset = word_info.get("Offset", 0) / 10000000.0
                                w_duration = word_info.get("Duration", 0) / 10000000.0
                                w_confidence = word_info.get("Confidence", 0.0)
                                
                                words.append(
                                    WordTimestamp(
                                        speaker=speaker_label,
                                        word=w_text,
                                        start=w_offset,
                                        end=w_offset + w_duration,
                                        confidence=w_confidence
                                    )
                                )
                    except Exception:
                        pass
                
                segments.append(
                    TranscriptSegment(
                        speaker=speaker_label,
                        start=start_sec,
                        end=end_sec,
                        text=text,
                        confidence=confidence
                    )
                )

        transcriber.transcribed.connect(transcribed_cb)
        transcriber.session_stopped.connect(session_stopped_cb)
        transcriber.canceled.connect(canceled_cb)
        
        transcriber.start_transcribing_async()
        
        # Wait for completion
        import time
        while not done:
            time.sleep(0.1)
            
        transcriber.stop_transcribing_async()
        
        segments.sort(key=lambda s: s.start)
        words.sort(key=lambda w: w.start)
        
        return TranscriptionResult(
            segments=segments,
            raw_response={},
            language=language,
            words=words
        )
