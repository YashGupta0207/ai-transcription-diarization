"""Real-time microphone and Windows WASAPI-loopback transcription capture."""
import json
import platform
import queue
import threading
import time

import numpy as np
import sounddevice as sd
import websocket
from PySide6.QtCore import QObject, Signal

try:
    import soundcard as sc
except ImportError:  # The UI falls back cleanly when the optional runtime is absent.
    sc = None


MICROPHONE_ONLY = "microphone"
SYSTEM_AUDIO_ONLY = "system"
MICROPHONE_AND_SYSTEM = "both"
MIC_SAMPLE_RATE = 16000
MIX_SAMPLE_RATE = 48000
CHANNELS = 1
MIC_BLOCK_SIZE = 4000  # 250 ms: preserves the existing microphone-only behavior.
MIX_BLOCK_SIZE = 4800  # 100 ms at 48 kHz, for low-latency loopback/mixing.
MAX_QUEUE_BLOCKS = 120  # Bounded: roughly 12 seconds in the mixed 48 kHz path.


class LiveTranscriber(QObject):
    """Capture a selected source and stream mono linear16 audio in background threads.

    Windows loopback is obtained from SoundCard's WASAPI backend.  Deepgram
    receives a single mixed channel in combined mode, which lets its existing
    streaming diarization identify turns from both local and remote speakers.
    """
    transcript_received = Signal(dict)
    saved = Signal(object)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, ws_url: str, audio_source: str = MICROPHONE_ONLY):
        super().__init__()
        self.ws_url = ws_url
        self.audio_source = audio_source
        self._ws = None
        self._audio_queue = queue.Queue(maxsize=MAX_QUEUE_BLOCKS)
        self._mic_queue = queue.Queue(maxsize=20)
        self._system_queue = queue.Queue(maxsize=20)
        self._recording = False
        self._paused = False
        self._ws_thread = None
        self._loopback_thread = None
        self._mixer_thread = None
        self._stream = None
        self._overflow_reported = False

    @property
    def sample_rate(self) -> int:
        return MIC_SAMPLE_RATE if self.audio_source == MICROPHONE_ONLY else MIX_SAMPLE_RATE

    @staticmethod
    def loopback_available() -> bool:
        """Probe the native endpoint without opening a recording stream."""
        if platform.system() != "Windows" or sc is None:
            return False
        try:
            speaker = sc.default_speaker()
            return sc.get_microphone(speaker.name, include_loopback=True) is not None
        except Exception:
            return False

    def start(self):
        self._recording = True
        self._paused = False
        self._ws_thread = threading.Thread(target=self._run_websocket, daemon=True, name="live-websocket")
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

    def _audio_callback(self, indata, _frames, _time_info, status):
        if status:
            self.error.emit(f"Microphone capture warning: {status}")
        if self._recording and not self._paused:
            samples = self._mono(indata)
            if self.audio_source == MICROPHONE_ONLY:
                self._enqueue_pcm(samples)
            else:
                self._put_latest(self._mic_queue, (time.monotonic(), samples))

    def _start_capture(self):
        if self.audio_source == MICROPHONE_ONLY:
            self._start_microphone_stream(MIC_SAMPLE_RATE, MIC_BLOCK_SIZE)
            return

        if not self.loopback_available():
            raise RuntimeError(
                "Windows WASAPI loopback is unavailable for the current output device. "
                "Continuing with microphone-only capture is recommended."
            )

        if self.audio_source == MICROPHONE_AND_SYSTEM:
            self._start_microphone_stream(MIX_SAMPLE_RATE, MIX_BLOCK_SIZE)
            self._mixer_thread = threading.Thread(target=self._mix_loop, daemon=True, name="live-audio-mixer")
            self._mixer_thread.start()

        self._loopback_thread = threading.Thread(
            target=self._loopback_loop, daemon=True, name="wasapi-loopback"
        )
        self._loopback_thread.start()

    def _start_microphone_stream(self, sample_rate, block_size):
        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=CHANNELS, dtype="float32",
            blocksize=block_size, callback=self._audio_callback,
        )
        self._stream.start()

    def _loopback_loop(self):
        try:
            # SoundCard maps this endpoint to native shared-mode WASAPI loopback.
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(speaker.name, include_loopback=True)
            with loopback.recorder(samplerate=MIX_SAMPLE_RATE) as recorder:
                while self._recording:
                    samples = self._mono(recorder.record(numframes=MIX_BLOCK_SIZE))
                    if not self._paused:
                        if self.audio_source == SYSTEM_AUDIO_ONLY:
                            self._enqueue_pcm(samples)
                        else:
                            self._put_latest(self._system_queue, (time.monotonic(), samples))
        except Exception as exc:
            # The initially selected endpoint can disappear (Bluetooth switch,
            # driver reset) after the non-blocking UI probe.  Keep the session
            # alive by changing to microphone-only capture at the already
            # negotiated 48 kHz rate.
            try:
                if self._recording and self.audio_source != MICROPHONE_ONLY:
                    if self._stream:
                        self._stream.stop()
                        self._stream.close()
                    self.audio_source = MICROPHONE_ONLY
                    self._start_microphone_stream(MIX_SAMPLE_RATE, MIX_BLOCK_SIZE)
                    self.error.emit(f"System audio unavailable; continuing with microphone only: {exc}")
                    return
            except Exception as fallback_exc:
                self.error.emit(f"System-audio loopback and microphone fallback failed: {fallback_exc}")
            self._recording = False

    def _mix_loop(self):
        """Pair timestamped, equal-size blocks; discard stale blocks to bound drift."""
        mic = system = None
        try:
            while self._recording or not self._mic_queue.empty() or not self._system_queue.empty():
                if mic is None:
                    mic = self._mic_queue.get(timeout=0.2)
                if system is None:
                    system = self._system_queue.get(timeout=0.2)
                skew = mic[0] - system[0]
                if skew < -0.15:
                    mic = None
                    continue
                if skew > 0.15:
                    system = None
                    continue
                mixed = self._mix(mic[1], system[1])
                self._enqueue_pcm(mixed)
                mic = system = None
        except queue.Empty:
            pass
        except Exception as exc:
            self.error.emit(f"Audio mixer stopped: {exc}")
            self._recording = False

    @staticmethod
    def _mono(samples):
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        return samples.reshape(-1)

    @staticmethod
    def _mix(microphone, system):
        frames = min(len(microphone), len(system))
        # Equal-power attenuation avoids clipping when both parties speak.
        mixed = (microphone[:frames] + system[:frames]) * 0.5
        return np.clip(mixed, -1.0, 1.0)

    @staticmethod
    def _put_latest(target_queue, item):
        try:
            target_queue.put_nowait(item)
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                pass
            target_queue.put_nowait(item)

    def _enqueue_pcm(self, samples):
        pcm_bytes = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        try:
            self._audio_queue.put_nowait(pcm_bytes)
        except queue.Full:
            if not self._overflow_reported:
                self._overflow_reported = True
                self.error.emit("Network cannot keep up with live audio; recording was stopped to avoid data loss.")
            self._recording = False

    def _run_websocket(self):
        try:
            # Keep a finite timeout for the initial TCP/WebSocket handshake so
            # a bad endpoint fails promptly.  websocket-client otherwise keeps
            # that same timeout for recv(), which incorrectly terminates a
            # healthy live session after ten seconds of silence (Deepgram has
            # no transcript event to send during that period).
            self._ws = websocket.create_connection(self.ws_url, timeout=10)
            self._ws.settimeout(None)
            sender_thread = threading.Thread(target=self._send_audio_loop, daemon=True, name="live-audio-sender")
            sender_thread.start()
            self._start_capture()
        except Exception as exc:
            self.error.emit(f"Could not start live transcription: {exc}")
            self._recording = False

        try:
            while self._recording:
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
        except Exception as exc:
            if self._recording:
                self.error.emit(f"Live connection closed: {exc}")
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
                chunk = self._audio_queue.get(timeout=0.2)
                if self._ws:
                    self._ws.send_binary(chunk)
            except queue.Empty:
                continue
            except Exception:
                self._recording = False
                break
        if self._ws:
            try:
                self._ws.send("stop")
            except Exception:
                pass
