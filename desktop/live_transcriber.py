"""
Live Transcription support for the desktop app.

Captures microphone audio with sounddevice (raw 16-bit PCM, 16kHz mono),
streams it over a WebSocket to the backend's /ws/live endpoint, and emits Qt
signals with incremental transcript text as it arrives. Runs entirely on a
background thread so the GUI never freezes while recording.

This module is fully additive - it does not import or modify main.py's
existing upload/job-list/export code paths.
"""
import json
import threading
import queue

import numpy as np
import sounddevice as sd
import websocket

from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 4000  # ~0.25s per chunk at 16kHz


class LiveTranscriber(QObject):
    """
    Emits:
      transcript_received(dict) - {"is_final": bool, "speaker": str, "text": str}
      saved(job_id or None)     - once the session is finalized server-side
      error(str)
      stopped()
    """
    transcript_received = Signal(dict)
    saved = Signal(object)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, ws_url: str):
        super().__init__()
        self.ws_url = ws_url
        self._ws = None
        self._audio_queue = queue.Queue()
        self._recording = False
        self._paused = False
        self._audio_thread = None
        self._ws_thread = None
        self._stream = None

    def start(self):
        self._recording = True
        self._paused = False
        self._ws_thread = threading.Thread(target=self._run_websocket, daemon=True)
        self._ws_thread.start()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        if self._ws:
            try:
                self._ws.send("stop")
            except Exception:
                pass

    def _audio_callback(self, indata, frames, time_info, status):
        if self._recording and not self._paused:
            pcm_bytes = (indata * 32767).astype(np.int16).tobytes()
            self._audio_queue.put(pcm_bytes)

    def _run_websocket(self):
        try:
            self._ws = websocket.create_connection(self.ws_url, timeout=10)
        except Exception as e:
            self.error.emit(f"Could not connect to live transcription: {e}")
            self.stopped.emit()
            return

        sender_thread = threading.Thread(target=self._send_audio_loop, daemon=True)
        sender_thread.start()

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="float32",
                blocksize=BLOCK_SIZE, callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            self.error.emit(f"Microphone unavailable: {e}")
            self._recording = False

        try:
            while True:
                raw = self._ws.recv()
                if not raw:
                    break
                data = json.loads(raw)
                if data.get("type") == "transcript":
                    self.transcript_received.emit(data)
                elif data.get("type") == "saved":
                    self.saved.emit(data.get("job_id"))
                    break
                elif data.get("type") == "error":
                    self.error.emit(data.get("message", "Unknown live error"))
        except Exception as e:
            self.error.emit(f"Live connection closed: {e}")
        finally:
            self._recording = False
            try:
                self._ws.close()
            except Exception:
                pass
            self.stopped.emit()

    def _send_audio_loop(self):
        while self._recording or not self._audio_queue.empty():
            try:
                chunk = self._audio_queue.get(timeout=0.5)
                if self._ws:
                    self._ws.send_binary(chunk)
            except queue.Empty:
                continue
            except Exception:
                break