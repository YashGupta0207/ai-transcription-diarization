"""
WebSocket endpoint for Live Transcription mode.

Proxies microphone audio to Azure Speech SDK's real-time streaming API and relays
partial/final transcript events back to the client as they arrive. When the
session stops, the finalized words/utterances are saved through the EXACT
SAME JobRepository used by file uploads - so a live session becomes a normal
completed Job, appears in the regular job list, and supports every existing
export format (txt/json/srt/vtt) automatically, with zero special casing
anywhere else in the app.

Supports linear16 audio format (raw 16-bit PCM, 16kHz mono).
"""
import json
import asyncio
import tempfile
import uuid
import wave
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session
import azure.cognitiveservices.speech as speechsdk

from app.config import settings
from app.database import SessionLocal
from app.repositories.job_repository import JobRepository
from app.models.job import JobStatus
from app.storage.factory import get_storage
from app.utils.media import segments_to_readable
from app.utils.logging_config import setup_logging

logger = setup_logging("live")

router = APIRouter(tags=["live"])


@router.websocket("/ws/live")
async def live_transcription(
    websocket: WebSocket,
    format: str = Query(default="linear16"),
    sample_rate: int = Query(default=16000),
):
    await websocket.accept()

    if format != "linear16":
        await websocket.send_json({"type": "error", "message": "Only linear16 format is supported for Azure live transcription"})
        await websocket.close()
        return

    if sample_rate not in (16000, 48000):
        await websocket.send_json({"type": "error", "message": "linear16 supports 16 kHz or 48 kHz audio"})
        await websocket.close()
        return

    if not settings.AZURE_SPEECH_KEY or not settings.AZURE_SPEECH_REGION:
        await websocket.send_json({"type": "error", "message": "AZURE_SPEECH_KEY not configured on server"})
        await websocket.close()
        return

    collected_words = []
    collected_segments = []
    recorded_audio = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")

    try:
        speech_config = speechsdk.SpeechConfig(subscription=settings.AZURE_SPEECH_KEY, region=settings.AZURE_SPEECH_REGION)
        speech_config.speech_recognition_language = "en-US"
        speech_config.output_format = speechsdk.OutputFormat.Detailed
        speech_config.request_word_level_timestamps()

        push_stream = speechsdk.audio.PushAudioInputStream(
            stream_format=speechsdk.audio.AudioStreamFormat(samples_per_second=sample_rate, bits_per_sample=16, channels=1)
        )
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
        
        transcriber = speechsdk.transcription.ConversationTranscriber(speech_config=speech_config, audio_config=audio_config)
        
        msg_queue = asyncio.Queue()
        
        def recognized_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                speaker_id = evt.result.speaker_id
                speaker_label = f"Speaker {speaker_id}" if speaker_id else "Speaker 1"
                text = evt.result.text
                
                confidence = 0.0
                words_payload = []
                properties = evt.result.properties
                json_result = properties.get(speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
                if json_result:
                    try:
                        parsed = json.loads(json_result)
                        n_best = parsed.get("NBest", [])
                        if n_best:
                            best = n_best[0]
                            confidence = best.get("Confidence", 0.0)
                            for w in best.get("Words", []):
                                words_payload.append({
                                    "speaker": speaker_label,
                                    "word": w.get("Word", ""),
                                    "start": w.get("Offset", 0) / 10000000.0,
                                    "end": (w.get("Offset", 0) + w.get("Duration", 0)) / 10000000.0,
                                    "confidence": w.get("Confidence", 0.0)
                                })
                    except Exception:
                        pass
                        
                msg_queue.put_nowait({
                    "type": "transcript",
                    "is_final": True,
                    "speaker": speaker_label,
                    "text": text,
                    "confidence": confidence,
                    "words": words_payload,
                    "start": evt.result.offset / 10000000.0,
                    "end": (evt.result.offset + evt.result.duration) / 10000000.0
                })
                
        def recognizing_cb(evt):
            if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                speaker_id = evt.result.speaker_id
                speaker_label = f"Speaker {speaker_id}" if speaker_id else "Speaker 1"
                text = evt.result.text
                msg_queue.put_nowait({
                    "type": "transcript",
                    "is_final": False,
                    "speaker": speaker_label,
                    "text": text,
                    "confidence": 0.0
                })
                
        def canceled_cb(evt):
            if evt.reason == speechsdk.CancellationReason.Error:
                msg_queue.put_nowait({"type": "error", "message": f"Canceled: {evt.error_details}"})
            msg_queue.put_nowait(None)
            
        def session_stopped_cb(evt):
            msg_queue.put_nowait(None)
            
        transcriber.transcribed.connect(recognized_cb)
        transcriber.transcribing.connect(recognizing_cb)
        transcriber.canceled.connect(canceled_cb)
        transcriber.session_stopped.connect(session_stopped_cb)
        
        transcriber.start_transcribing_async()
        
        async def receive_audio():
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        chunk = message["bytes"]
                        recorded_audio.write(chunk)
                        push_stream.write(chunk)
                    elif message.get("text") is not None:
                        if message["text"] == "stop":
                            push_stream.close()
                            break
            except WebSocketDisconnect:
                push_stream.close()
            except Exception as e:
                logger.warning(f"Live audio forwarding stopped: {e}")
                push_stream.close()
                
        async def send_results():
            try:
                while True:
                    msg = await msg_queue.get()
                    if msg is None:
                        break
                    if msg.get("type") == "error":
                        await websocket.send_json(msg)
                        break
                    
                    await websocket.send_json({
                        "type": "transcript",
                        "is_final": msg["is_final"],
                        "speaker": msg["speaker"],
                        "text": msg["text"],
                        "confidence": msg["confidence"]
                    })
                    
                    if msg["is_final"]:
                        collected_segments.append({
                            "speaker": msg["speaker"],
                            "start": msg["start"],
                            "end": msg["end"],
                            "text": msg["text"],
                            "confidence": msg["confidence"]
                        })
                        for w in msg.get("words", []):
                            collected_words.append(w)
            except Exception as e:
                logger.warning(f"Live result relay stopped: {e}")
                
        await asyncio.gather(receive_audio(), send_results())
        
        transcriber.stop_transcribing_async()

    except Exception as e:
        logger.error(f"Live transcription session failed: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass

    db: Session = SessionLocal()
    persisted_audio = None
    try:
        repo = JobRepository(db)
        if collected_segments:
            storage = get_storage()
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H-%M-%S")
            audio_file, suffix, content_type = _finalize_live_audio(recorded_audio, format, sample_rate)
            persisted_audio = audio_file
            size_bytes = audio_file.seek(0, 2)
            audio_file.seek(0)

            filename = f"Live Recording {timestamp}{suffix}"
            job = repo.create_job(original_filename=filename, provider="azure-live")
            storage_key = f"uploads/{job.id}/{uuid.uuid4().hex}{suffix}"
            storage.save(storage_key, audio_file)
            repo.attach_file(job, storage_key, settings.STORAGE_BACKEND, content_type, size_bytes)
            repo.set_status(job, JobStatus.PROCESSING)
            readable = segments_to_readable(collected_segments)
            repo.save_results(job, collected_segments, readable, {"live": True}, words=collected_words)
            repo.set_status(job, JobStatus.COMPLETED)
            await websocket.send_json({"type": "saved", "job_id": job.id})
        else:
            await websocket.send_json({"type": "saved", "job_id": None})
    except Exception as e:
        logger.error(f"Failed to save live transcription job: {e}")
    finally:
        db.close()
        if persisted_audio is not None and persisted_audio is not recorded_audio:
            persisted_audio.close()
        recorded_audio.close()

    try:
        await websocket.close()
    except Exception:
        pass


def _finalize_live_audio(recorded_audio, audio_format: str, sample_rate: int = 16000):
    """Return a playable persisted stream for either supported live client."""
    recorded_audio.seek(0)
    if audio_format != "linear16":
        return recorded_audio, ".webm", "audio/webm"

    # Wrap raw 16-bit PCM in a WAV container so its saved conversation uses
    # exactly the same playback pipeline as uploaded audio files.
    wav_file = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    with wave.open(wav_file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(recorded_audio.read())
    wav_file.seek(0)
    return wav_file, ".wav", "audio/wav"
