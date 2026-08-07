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

    if not settings.GATEWAY_API_KEY:
        await websocket.send_json({"type": "error", "message": "GATEWAY_API_KEY not configured on server"})
        await websocket.close()
        return

    collected_words = []
    collected_segments = []
    recorded_audio = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")

    from app.ai_gateway_sdk.dxai import DXAILiveClient

    dxai_client = DXAILiveClient(
        api_key=settings.GATEWAY_API_KEY,
        base_url=settings.GATEWAY_BASE_URL,
        provider=settings.GATEWAY_TARGET_PROVIDER if settings.SPEECH_PROVIDER == "gateway" else settings.SPEECH_PROVIDER,
        sample_rate=sample_rate,
        format=format
    )

    try:
        await dxai_client.connect()
        
        async def receive_audio():
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        chunk = message["bytes"]
                        recorded_audio.write(chunk)
                        await dxai_client.send_audio(chunk)
                    elif message.get("text") is not None:
                        if message["text"] == "stop":
                            await dxai_client.send_text("stop")
                            await dxai_client.close()
                            break
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.warning(f"Live audio forwarding stopped: {e}")
                
        async def send_results():
            try:
                async for msg in dxai_client.receive_events():
                    if msg.get("type") == "error":
                        await websocket.send_json(msg)
                        break
                    
                    await websocket.send_json(msg)
                    
                    if msg.get("type") == "transcript" and msg.get("is_final"):
                        collected_segments.append({
                            "speaker": msg.get("speaker", "Speaker 1"),
                            "start": msg.get("start", 0.0),
                            "end": msg.get("end", 0.0),
                            "text": msg.get("text", ""),
                            "confidence": msg.get("confidence", 0.0)
                        })
                        for w in msg.get("words", []):
                            collected_words.append(w)
            except Exception as e:
                logger.warning(f"Live result relay stopped: {e}")
                
        await asyncio.gather(receive_audio(), send_results())
        
        await dxai_client.close()

    except Exception as e:
        logger.error(f"Live transcription session failed: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        finally:
            await dxai_client.close()

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
