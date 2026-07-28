"""
WebSocket endpoint for Live Transcription mode.

Proxies microphone audio to Deepgram's real-time streaming API and relays
partial/final transcript events back to the client as they arrive. When the
session stops, the finalized words/utterances are saved through the EXACT
SAME JobRepository used by file uploads - so a live session becomes a normal
completed Job, appears in the regular job list, and supports every existing
export format (txt/json/srt/vtt) automatically, with zero special casing
anywhere else in the app.

Supports two audio sources via a query parameter, since browsers and desktop
clients send different raw formats:
  - ?format=webm  (default) - browser MediaRecorder output (audio/webm;opus)
  - ?format=linear16        - raw 16-bit PCM, e.g. from the desktop app's
                              microphone capture (sounddevice), 16kHz mono

This file is entirely additive: it does not import from or modify jobs.py,
job_service.py, or the RQ queue in any way.
"""
import json
import asyncio
import tempfile
import uuid
import wave
from datetime import datetime

import websockets as ws_client
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.repositories.job_repository import JobRepository
from app.models.job import JobStatus
from app.storage.factory import get_storage
from app.utils.media import segments_to_readable
from app.utils.logging_config import setup_logging

logger = setup_logging("live")

router = APIRouter(tags=["live"])


def build_deepgram_live_url(audio_format: str) -> str:
    base = (
        "wss://api.deepgram.com/v1/listen"
        "?punctuate=true&diarize=true&interim_results=true&smart_format=true&model=nova-2"
    )
    if audio_format == "linear16":
        base += "&encoding=linear16&sample_rate=16000&channels=1"
    return base


@router.websocket("/ws/live")
async def live_transcription(websocket: WebSocket, format: str = Query(default="webm")):
    await websocket.accept()

    if not settings.DEEPGRAM_API_KEY:
        await websocket.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured on server"})
        await websocket.close()
        return

    collected_words = []
    collected_segments = []
    # Keep the browser's WebM (or desktop linear PCM) until Deepgram finalizes
    # the session. SpooledTemporaryFile avoids retaining long recordings only
    # in RAM while still presenting the normal BinaryIO interface to storage.
    recorded_audio = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")

    try:
        async with ws_client.connect(
            build_deepgram_live_url(format),
            # websockets 14+ renamed the legacy ``extra_headers`` argument.
            # Pinning the supported range in requirements keeps this aligned
            # with the installed client API instead of silently failing before
            # a Deepgram connection is opened.
            additional_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            ping_interval=5,
        ) as dg_ws:

            async def forward_audio_to_deepgram():
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            break
                        if message.get("bytes") is not None:
                            chunk = message["bytes"]
                            recorded_audio.write(chunk)
                            await dg_ws.send(chunk)
                        elif message.get("text") is not None:
                            if message["text"] == "stop":
                                await dg_ws.send(json.dumps({"type": "CloseStream"}))
                                break
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    logger.warning(f"Live audio forwarding stopped: {e}")

            async def relay_deepgram_results():
                try:
                    async for raw in dg_ws:
                        data = json.loads(raw)
                        channel = data.get("channel", {})
                        alternatives = channel.get("alternatives", [])
                        if not alternatives:
                            continue
                        alt = alternatives[0]
                        text = alt.get("transcript", "")
                        if not text:
                            continue

                        is_final = data.get("is_final", False)
                        words_payload = alt.get("words", [])
                        speaker_idx = words_payload[0].get("speaker", 0) if words_payload else 0
                        speaker_label = f"Speaker {speaker_idx + 1}"

                        await websocket.send_json({
                            "type": "transcript",
                            "is_final": is_final,
                            "speaker": speaker_label,
                            "text": text,
                            "confidence": alt.get("confidence"),
                        })

                        if is_final:
                            start = words_payload[0]["start"] if words_payload else 0.0
                            end = words_payload[-1]["end"] if words_payload else 0.0
                            collected_segments.append({
                                "speaker": speaker_label,
                                "start": start,
                                "end": end,
                                "text": text,
                                "confidence": alt.get("confidence"),
                            })
                            for w in words_payload:
                                collected_words.append({
                                    "speaker": speaker_label,
                                    "word": w.get("punctuated_word") or w.get("word", ""),
                                    "start": w.get("start", 0.0),
                                    "end": w.get("end", 0.0),
                                    "confidence": w.get("confidence"),
                                })
                except Exception as e:
                    logger.warning(f"Live result relay stopped: {e}")

            await asyncio.gather(forward_audio_to_deepgram(), relay_deepgram_results())

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
            audio_file, suffix, content_type = _finalize_live_audio(recorded_audio, format)
            persisted_audio = audio_file
            size_bytes = audio_file.seek(0, 2)
            audio_file.seek(0)

            filename = f"Live Recording {timestamp}{suffix}"
            job = repo.create_job(original_filename=filename, provider="deepgram-live")
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


def _finalize_live_audio(recorded_audio, audio_format: str):
    """Return a playable persisted stream for either supported live client."""
    recorded_audio.seek(0)
    if audio_format != "linear16":
        return recorded_audio, ".webm", "audio/webm"

    # The PySide client sends raw 16-bit PCM. Wrap it in a WAV container so
    # its saved conversation uses exactly the same playback pipeline as
    # uploaded audio files.
    wav_file = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    with wave.open(wav_file, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(recorded_audio.read())
    wav_file.seek(0)
    return wav_file, ".wav", "audio/wav"
