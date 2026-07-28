"""
WebSocket endpoint for Live Transcription mode.

Proxies browser microphone audio to Deepgram's real-time streaming API and
relays partial/final transcript events back to the browser as they arrive.
When the session stops, the finalized words/utterances are saved through the
EXACT SAME JobRepository used by file uploads - so a live session becomes a
normal completed Job, appears in the regular job list, and supports every
existing export format (txt/json/srt/vtt) automatically, with zero special
casing anywhere else in the app.

This file is entirely additive: it does not import from or modify jobs.py,
job_service.py, or the RQ queue in any way.
"""
import json
import asyncio
from datetime import datetime

import websockets as ws_client
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.repositories.job_repository import JobRepository
from app.models.job import JobStatus
from app.utils.media import segments_to_readable
from app.utils.logging_config import setup_logging

logger = setup_logging("live")

router = APIRouter(tags=["live"])

DEEPGRAM_LIVE_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?punctuate=true&diarize=true&interim_results=true&smart_format=true&model=nova-2"
)


@router.websocket("/ws/live")
async def live_transcription(websocket: WebSocket):
    await websocket.accept()

    if not settings.DEEPGRAM_API_KEY:
        await websocket.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured on server"})
        await websocket.close()
        return

    # Accumulates finalized words/utterances as they stream in, so a normal
    # Job/Segment/TranscriptWord record can be saved once recording stops.
    collected_words = []
    collected_segments = []

    try:
        async with ws_client.connect(
            DEEPGRAM_LIVE_URL,
            extra_headers={"Authorization": f"Token {settings.DEEPGRAM_API_KEY}"},
            ping_interval=5,
        ) as dg_ws:

            async def forward_audio_to_deepgram():
                """Relays raw audio bytes from the browser straight through to Deepgram."""
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("type") == "websocket.disconnect":
                            break
                        if message.get("bytes") is not None:
                            await dg_ws.send(message["bytes"])
                        elif message.get("text") is not None:
                            if message["text"] == "stop":
                                await dg_ws.send(json.dumps({"type": "CloseStream"}))
                                break
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    logger.warning(f"Live audio forwarding stopped: {e}")

            async def relay_deepgram_results():
                """Relays Deepgram's transcript events back to the browser in real
                time, and keeps a running record of finalized text for saving later."""
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

    # Save whatever was collected as a normal completed Job.
    db: Session = SessionLocal()
    try:
        repo = JobRepository(db)
        if collected_segments:
            filename = f"Live Recording {datetime.utcnow().strftime('%Y-%m-%d %H-%M-%S')}"
            job = repo.create_job(original_filename=filename, provider="deepgram-live")
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

    try:
        await websocket.close()
    except Exception:
        pass