"""
Minimal PySide6 desktop client.

Design intent (per project requirements): NO local transcription/diarization,
NO fancy UI for the core upload/job list. The app only uploads, polls, and
displays results. It is safe to close this app entirely mid-job - the
backend/worker keep processing independently, and reopening the app just
resumes polling via GET /jobs.

Adds two additive features, kept in separate dialog windows so the existing
upload/job-list/export flow is completely untouched:
  - Live Transcription (mic capture -> live_transcriber.LiveTranscriber)
  - Playback Verification (speaker-card transcript with word-level highlight
    synced to audio playback, similar in spirit to commercial transcript
    tools: one rounded card per speaker turn, active word highlighted as it
    plays, click any word to jump playback there)
"""
import sys
import os
import tempfile
import html

from PySide6.QtCore import QTimer, Qt, QUrl, QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
    QLabel, QMessageBox, QComboBox, QDialog, QSlider, QScrollArea, QFrame, QLineEdit, QProgressBar
)

from client import BackendClient
from live_transcriber import (
    LiveTranscriber, MICROPHONE_ONLY, SYSTEM_AUDIO_ONLY, MICROPHONE_AND_SYSTEM,
)
from transcript_sync import active_word_index, format_timestamp
from async_worker import run_in_background

POLL_INTERVAL_MS = 4000
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TranscribeApp Desktop")
        self.resize(900, 600)

        self.client = BackendClient()
        self.jobs_by_row = {}
        self._refresh_in_flight = False
        self._result_request_id = 0
        self._active_job_signature = None
        self._settings = QSettings("TranscribeApp", "TranscribeApp")
        self._upload_started_at = None

        root = QWidget()
        layout = QHBoxLayout(root)

        # ---- Left panel: job list + controls ----
        left = QVBoxLayout()
        self.upload_btn = QPushButton("Upload Audio/Video File")
        self.upload_btn.clicked.connect(self.on_upload)
        left.addWidget(self.upload_btn)
        self.upload_progress = QProgressBar()
        self.upload_progress.setVisible(False)
        self.upload_progress.setTextVisible(True)
        left.addWidget(self.upload_progress)
        self.upload_details = QLabel()
        self.upload_details.setWordWrap(True)
        self.upload_details.setVisible(False)
        left.addWidget(self.upload_details)

        self.live_btn = QPushButton("🎙 Live Transcription")
        self.live_btn.clicked.connect(self.on_open_live)
        left.addWidget(self.live_btn)

        self.job_list = QListWidget()
        self.job_list.currentRowChanged.connect(self.on_select_job)
        left.addWidget(self.job_list)

        btn_row = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.on_cancel)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.on_delete)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.delete_btn)
        left.addLayout(btn_row)

        layout.addLayout(left, stretch=1)

        # ---- Right panel: status + transcript viewer ----
        right = QVBoxLayout()
        self.status_label = QLabel("Select a job to view details")
        right.addWidget(self.status_label)

        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        right.addWidget(self.transcript_view, stretch=1)

        self.playback_btn = QPushButton("Verify Playback")
        self.playback_btn.clicked.connect(self.on_open_playback)
        self.playback_btn.setEnabled(False)
        right.addWidget(self.playback_btn)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export as:"))
        self.export_format = QComboBox()
        self.export_format.addItems(["txt", "json", "srt", "vtt"])
        export_row.addWidget(self.export_format)
        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.on_export)
        export_row.addWidget(self.export_btn)
        right.addLayout(export_row)

        layout.addLayout(right, stretch=2)

        self.setCentralWidget(root)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_jobs)
        self.refresh_timer.start(POLL_INTERVAL_MS)
        self.refresh_jobs()

    # ---------------- Existing actions (unchanged) ----------------

    def on_upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select audio/video file", "",
            "Media Files (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.mp4 *.mov *.mkv *.avi)"
        )
        if not path:
            return
        self.upload_btn.setEnabled(False)
        total_bytes = os.path.getsize(path)
        self._upload_started_at = None
        self.upload_progress.setRange(0, 100)
        self.upload_progress.setValue(0)
        self.upload_progress.setVisible(True)
        self.upload_details.setText(f"Uploading {os.path.basename(path)}\n0 B / {self._format_bytes(total_bytes)}")
        self.upload_details.setVisible(True)
        self.status_label.setText("Uploading…")
        signals = run_in_background(self.client.upload_file, path, progress=True)
        signals.progress.connect(self._on_upload_progress)
        signals.result.connect(lambda result: self._on_upload_complete(result, path))
        signals.error.connect(lambda error: QMessageBox.critical(self, "Upload failed", error))
        signals.finished.connect(self._finish_upload)

    def _on_upload_progress(self, progress):
        sent = progress.get("sent", 0)
        total = progress.get("total", 0)
        elapsed = progress.get("elapsed", 0.0)
        if self._upload_started_at is None and sent:
            self._upload_started_at = elapsed
        percent = int((sent * 100 / total) if total else 0)
        self.upload_progress.setValue(percent)
        speed = sent / elapsed if elapsed > 0 else 0
        remaining = (total - sent) / speed if speed > 0 else None
        eta = f"\nETA: {self._format_duration(remaining)}" if remaining is not None else ""
        self.upload_details.setText(
            f"Uploading {percent}%\n{self._format_bytes(sent)} / {self._format_bytes(total)}"
            f"\nSpeed: {self._format_bytes(speed)}/s{eta}"
        )

    def _finish_upload(self):
        self.upload_btn.setEnabled(True)

    def _on_upload_complete(self, result, path):
        self._settings.setValue(f"local_media/{result['job_id']}", os.path.abspath(path))
        self.upload_progress.setValue(100)
        self.upload_details.setText("Upload complete — waiting in queue…")
        self.status_label.setText("Waiting in queue…")
        QMessageBox.information(self, "Uploaded", f"Job queued: {result['job_id']}")
        self.refresh_jobs()

    @staticmethod
    def _format_bytes(value):
        value = float(value or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024

    @staticmethod
    def _format_duration(seconds):
        seconds = max(0, int(seconds))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def refresh_jobs(self):
        # Do not queue overlapping polls while an earlier request is still in
        # flight; a slow network must never block Qt's event loop.
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        signals = run_in_background(self.client.list_jobs)
        signals.result.connect(self._apply_job_list)
        signals.finished.connect(self._finish_refresh)

    def _finish_refresh(self):
        self._refresh_in_flight = False

    def _apply_job_list(self, data):

        self.job_list.blockSignals(True)
        current_row = self.job_list.currentRow()
        self.job_list.clear()
        self.jobs_by_row = {}

        for i, job in enumerate(data.get("jobs", [])):
            label = f"[{job['status'].upper()}]  {job['original_filename']}"
            item = QListWidgetItem(label)
            self.job_list.addItem(item)
            self.jobs_by_row[i] = job

        self.job_list.blockSignals(False)
        if 0 <= current_row < self.job_list.count():
            self.job_list.setCurrentRow(current_row)

    def current_job_id(self):
        row = self.job_list.currentRow()
        job = self.jobs_by_row.get(row)
        return job["id"] if job else None

    def on_select_job(self, row: int):
        job = self.jobs_by_row.get(row)
        if not job:
            return
        # The four-second job poll repopulates the list and restores its
        # selection.  Do not treat that restoration as a new user selection:
        # repeatedly setting "Loading transcript" was the visible blink.
        signature = (job["id"], job["status"], job.get("updated_at"))
        self.status_label.setText(
            f"Job {job['id']}  |  Status: {job['status']}  |  Provider: {job['provider']}"
        )
        self.playback_btn.setEnabled(job["status"] == "completed")
        if signature == self._active_job_signature:
            return
        self._active_job_signature = signature
        if job["status"] == "completed":
            self._result_request_id += 1
            request_id = self._result_request_id
            self.transcript_view.setPlainText("Loading transcript…")
            signals = run_in_background(self.client.get_result, job["id"])
            signals.result.connect(lambda result: self._show_transcript(request_id, signature, result))
            signals.error.connect(lambda error: self._show_transcript_error(request_id, signature, error))
        elif job["status"] == "failed":
            self.transcript_view.setPlainText(f"Job failed: {job.get('error_message', 'unknown error')}")
        else:
            self.transcript_view.setPlainText("Processing on the server - this window can be closed safely.")

    def _show_transcript(self, request_id, signature, result):
        if request_id == self._result_request_id:
            self.transcript_view.setPlainText(result.get("readable_transcript") or "(empty transcript)")

    def _show_transcript_error(self, request_id, signature, error):
        if request_id == self._result_request_id:
            self.transcript_view.setPlainText(f"Error loading transcript: {error}")
            # Permit a later poll to retry a transient failed request.
            if self._active_job_signature == signature:
                self._active_job_signature = None

    def on_cancel(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        self.cancel_btn.setEnabled(False)
        signals = run_in_background(self.client.cancel, job_id)
        signals.result.connect(lambda _result: self.refresh_jobs())
        signals.error.connect(lambda error: QMessageBox.critical(self, "Cancel failed", error))
        signals.finished.connect(lambda: self.cancel_btn.setEnabled(True))

    def on_delete(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        self.delete_btn.setEnabled(False)
        signals = run_in_background(self.client.delete, job_id)
        signals.result.connect(self._on_delete_complete)
        signals.error.connect(lambda error: QMessageBox.critical(self, "Delete failed", error))
        signals.finished.connect(lambda: self.delete_btn.setEnabled(True))

    def _on_delete_complete(self, _result):
        job_id = self.current_job_id()
        if job_id:
            self._settings.remove(f"local_media/{job_id}")
        self.refresh_jobs()
        self.transcript_view.clear()

    def on_export(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        fmt = self.export_format.currentText()
        save_path, _ = QFileDialog.getSaveFileName(self, "Save export", f"transcript.{fmt}")
        if save_path:
            self.export_btn.setEnabled(False)
            signals = run_in_background(self._export_to_file, job_id, fmt, save_path)
            signals.result.connect(lambda _result: QMessageBox.information(self, "Saved", f"Exported to {save_path}"))
            signals.error.connect(lambda error: QMessageBox.critical(self, "Export failed", error))
            signals.finished.connect(lambda: self.export_btn.setEnabled(True))

    def _export_to_file(self, job_id, fmt, save_path):
        content = self.client.export(job_id, fmt)
        with open(save_path, "w", encoding="utf-8") as output:
            output.write(content)

    # ---------------- New: Live Transcription ----------------

    def on_open_live(self):
        dlg = LiveTranscriptionDialog(self.client, self)
        dlg.exec()
        self.refresh_jobs()

    # ---------------- New: Playback Verification ----------------

    def on_open_playback(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        job = self.jobs_by_row.get(self.job_list.currentRow())
        filename = job["original_filename"] if job else ""
        original_file_path = self._settings.value(f"local_media/{job_id}", "")
        dlg = PlaybackDialog(self.client, job_id, filename, original_file_path, self)
        dlg.exec()


class LiveTranscriptionDialog(QDialog):
    def __init__(self, client: BackendClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Transcription")
        self.resize(600, 500)
        self.client = client
        self.transcriber = None
        self.settings = QSettings("TranscribeApp", "TranscribeApp")

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Not recording")
        layout.addWidget(self.status_label)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Audio source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Microphone only", MICROPHONE_ONLY)
        self.source_combo.addItem("System audio only (Windows)", SYSTEM_AUDIO_ONLY)
        self.source_combo.addItem("Microphone + system audio (Windows)", MICROPHONE_AND_SYSTEM)
        saved_source = self.settings.value("live_audio_source", MICROPHONE_ONLY)
        saved_index = self.source_combo.findData(saved_source)
        self.source_combo.setCurrentIndex(max(0, saved_index))
        source_row.addWidget(self.source_combo, stretch=1)
        layout.addLayout(source_row)

        self.transcript_view = QTextEdit()
        self.transcript_view.setReadOnly(True)
        layout.addWidget(self.transcript_view, stretch=1)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start Recording")
        self.start_btn.clicked.connect(self.on_start)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.on_pause)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QPushButton("Stop && Save")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row)

        self._current_speaker = None
        self._paused = False
        # Positions delimit the one provisional Deepgram hypothesis at the end
        # of the document.  Finalized text is never selected or redrawn.
        self._interim_start = None
        self._interim_end = None

    def on_start(self):
        try:
            source = self.source_combo.currentData()
            self.settings.setValue("live_audio_source", source)
            if source != MICROPHONE_ONLY and not LiveTranscriber.loopback_available():
                QMessageBox.warning(
                    self,
                    "System audio unavailable",
                    "Windows WASAPI loopback is unavailable for the current output device. "
                    "Recording will continue with microphone-only audio.",
                )
                source = MICROPHONE_ONLY
            sample_rate = 16000 if source == MICROPHONE_ONLY else 48000
            self.transcriber = LiveTranscriber(self.client.live_ws_url(sample_rate), source)
            # The capture/WebSocket code emits from worker threads.  Explicit
            # queued connections guarantee all widget writes run on Qt's GUI
            # thread, regardless of a sender object's thread affinity.
            self.transcriber.transcript_received.connect(self.on_transcript, Qt.QueuedConnection)
            self.transcriber.saved.connect(self.on_saved, Qt.QueuedConnection)
            self.transcriber.error.connect(self.on_error, Qt.QueuedConnection)
            self.transcriber.stopped.connect(self.on_stopped, Qt.QueuedConnection)
            self.transcriber.start()
            self.status_label.setText("Recording…")
            self.start_btn.setEnabled(False)
            self.source_combo.setEnabled(False)
            self.pause_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "Live transcription failed", str(e))

    def on_pause(self):
        if not self.transcriber:
            return
        if not self._paused:
            self.transcriber.pause()
            self._paused = True
            self.pause_btn.setText("Resume")
            self.status_label.setText("Paused")
        else:
            self.transcriber.resume()
            self._paused = False
            self.pause_btn.setText("Pause")
            self.status_label.setText("Recording…")

    def on_stop(self):
        if self.transcriber:
            self.transcriber.stop()
        self.status_label.setText("Finalizing…")
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)

    def on_transcript(self, data: dict):
        """Apply one Deepgram event without clearing/rebuilding the document.

        Interim results are alternative hypotheses for the same unfinished
        utterance.  They replace only the dedicated trailing range.  A final
        result first removes that range, then becomes permanent text.
        """
        text = str(data.get("text", "")).strip()
        if not text:
            return
        speaker = str(data.get("speaker") or "Speaker 1")

        if data.get("is_final"):
            self._remove_interim()
            self._append_segment(speaker, text)
            return

        self._replace_interim(speaker, text)

    def _append_segment(self, speaker: str, text: str):
        cursor = self.transcript_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        if speaker != self._current_speaker:
            cursor.insertText(f"\n{speaker}\n")
            self._current_speaker = speaker
        cursor.insertText(text + " ")
        cursor.endEditBlock()
        self.transcript_view.setTextCursor(cursor)

    def _replace_interim(self, speaker: str, text: str):
        self._remove_interim()
        cursor = self.transcript_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.beginEditBlock()
        self._interim_start = cursor.position()
        # The provisional speaker label is inside the same range, so finalizing
        # cannot leave a duplicate label behind.
        prefix = f"\n{speaker}\n" if speaker != self._current_speaker else ""
        cursor.insertText(prefix + text + " ")
        self._interim_end = cursor.position()
        cursor.endEditBlock()
        self.transcript_view.setTextCursor(cursor)

    def _remove_interim(self):
        if self._interim_start is None or self._interim_end is None:
            return
        cursor = self.transcript_view.textCursor()
        cursor.setPosition(self._interim_start)
        cursor.setPosition(self._interim_end, QTextCursor.KeepAnchor)
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.endEditBlock()
        self.transcript_view.setTextCursor(cursor)
        self._interim_start = None
        self._interim_end = None

    def on_saved(self, job_id):
        if job_id:
            QMessageBox.information(self, "Saved", "Live session saved to your job list.")
        self.status_label.setText("Not recording")
        self.start_btn.setEnabled(True)
        self.source_combo.setEnabled(True)

    def on_error(self, message: str):
        QMessageBox.critical(self, "Live transcription error", message)

    def on_stopped(self):
        self.status_label.setText("Not recording")
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.source_combo.setEnabled(True)


class SpeakerCard(QFrame):
    """
    One rounded card per contiguous speaker turn (matches the reference
    look: colored speaker label + rounded bordered box). Each word inside is
    an individually clickable label; the active word gets highlighted as
    playback passes its timestamp.
    """
    def __init__(self, speaker: str, words: list, on_word_click, parent=None):
        super().__init__(parent)
        self.words = words  # list of dicts: {"word","start","end", global_index}
        self._word_indexes = {word["global_index"] for word in words}
        self.on_word_click = on_word_click
        self.word_labels = []

        self.setObjectName("speakerCard")
        self._active = False
        self._search_term = ""
        self._apply_style()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)

        title = QLabel(f"{speaker}  ·  {format_timestamp(words[0]['start'])} – {format_timestamp(words[-1]['end'])}")
        title.setStyleSheet("color:#5b8cff; font-weight:700; font-size:12px; text-transform:uppercase;")
        outer.addWidget(title)

        # Flow-style wrapping of word labels using a simple QHBoxLayout-in-QVBoxLayout
        # wrap approach: use a QLabel with rich text instead, simpler and
        # still supports per-word click via linkActivated.
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.RichText)
        self.text_label.setStyleSheet("font-size:14px; color:#e7e9ee;")
        self.text_label.linkActivated.connect(self._on_link)
        outer.addWidget(self.text_label)

        self.active_global_index = -1
        self.render_html()

    def _on_link(self, link: str):
        idx = int(link)
        self.on_word_click(idx)

    def mousePressEvent(self, event):
        """The card itself is a clickable transcript turn/timeline entry."""
        if event.button() == Qt.LeftButton and self.words:
            self.on_word_click(self.words[0]["global_index"])
            event.accept()
            return
        super().mousePressEvent(event)

    def render_html(self):
        parts = []
        for w in self.words:
            word = html.escape(str(w["word"]))
            is_match = self._search_term and self._search_term in str(w["word"]).lower()
            if w["global_index"] == self.active_global_index:
                parts.append(
                    f'<a href="{w["global_index"]}" style="background:#5b8cff;color:white;'
                    f'border-radius:3px;padding:1px 3px;text-decoration:none;">{word}</a>'
                )
            else:
                background = "background:#f2c94c;color:#16191f;border-radius:3px;padding:1px 3px;" if is_match else ""
                parts.append(
                    f'<a href="{w["global_index"]}" style="{background}color:#e7e9ee;text-decoration:none;">{word}</a>'
                )
        self.text_label.setText(" ".join(parts))

    def _apply_style(self):
        active = "#273b63" if self._active else "#1e222b"
        border = "#5b8cff" if self._active else "#2a2f3a"
        self.setStyleSheet(f"QFrame#speakerCard {{ background: {active}; border: 2px solid {border}; border-radius: 10px; }}")

    def set_search_term(self, value: str):
        value = value.strip().lower()
        if value != self._search_term:
            self._search_term = value
            self.render_html()

    def set_active_word(self, global_index: int) -> bool:
        """Returns True if this card contains the active word (used by the
        parent dialog to know when to auto-scroll to this card)."""
        contains = global_index in self._word_indexes
        if self.active_global_index != global_index:
            self.active_global_index = global_index if contains else -1
            self.render_html()
        if self._active != contains:
            self._active = contains
            self._apply_style()
        return contains


class PlaybackDialog(QDialog):
    def __init__(self, client: BackendClient, job_id: str, filename: str, original_file_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verify Playback")
        self.resize(760, 620)
        self.client = client
        self.job_id = job_id
        self.filename = filename
        self.original_file_path = original_file_path or ""
        self.words = []
        self.word_starts = []
        self.cards = []
        self.card_by_word_index = {}
        self.active_card = None
        self.active_index = -1
        self._temp_audio_path = None
        self._pending_card_groups = []
        self._search_card_index = 0
        self._search_term = ""
        self._card_build_timer = QTimer(self)
        self._card_build_timer.timeout.connect(self._build_next_card_batch)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search_batch)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel(f"Verify Playback — {filename}")
        title.setStyleSheet("font-weight:700; font-size:15px;")
        header.addWidget(title)
        header.addStretch()
        self.locate_media_btn = QPushButton("Locate original media…")
        self.locate_media_btn.clicked.connect(self.on_locate_media)
        self.locate_media_btn.setVisible(False)
        header.addWidget(self.locate_media_btn)
        layout.addLayout(header)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find words in this transcript (Ctrl+F)")
        self.search_input.textChanged.connect(self.on_search_changed)
        search_row.addWidget(self.search_input)
        self.playback_info = QLabel("Speaker: —  |  Word: —")
        search_row.addWidget(self.playback_info)
        layout.addLayout(search_row)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll_area.setWidget(self.cards_container)
        layout.addWidget(self.scroll_area, stretch=1)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.on_play_pause)
        self.back_btn = QPushButton("-10s")
        self.back_btn.clicked.connect(lambda: self.seek_relative(-10))
        self.fwd_btn = QPushButton("+10s")
        self.fwd_btn.clicked.connect(lambda: self.seek_relative(10))
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.sliderMoved.connect(self.on_slider_moved)
        self.time_label = QLabel("0:00 / 0:00")
        controls.addWidget(self.play_btn)
        controls.addWidget(self.back_btn)
        controls.addWidget(self.fwd_btn)
        controls.addWidget(self.seek_slider, stretch=1)
        controls.addWidget(self.time_label)
        layout.addLayout(controls)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "1x", "1.25x", "1.5x", "2x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentTextChanged.connect(self.on_speed_changed)
        speed_row.addWidget(self.speed_combo)
        speed_row.addWidget(QLabel("Volume:"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        speed_row.addWidget(self.volume_slider)
        layout.addLayout(speed_row)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)

        self._load_audio_and_words()

    def _load_audio_and_words(self):
        self.playback_info.setText("Loading transcript…")
        word_signals = run_in_background(self._fetch_playback_words)
        word_signals.result.connect(self._apply_playback_words)
        word_signals.error.connect(lambda error: QMessageBox.warning(self, "Transcript unavailable", error))
        self._load_local_media()

    def _fetch_playback_words(self):
        try:
            raw_words = self.client.get_words(self.job_id)
        except Exception:
            raw_words = []
        raw_words = [w for w in raw_words if self._is_valid_word(w)]
        raw_words.sort(key=lambda word: float(word["start"]))
        for idx, word in enumerate(raw_words):
            word["global_index"] = idx

        return raw_words

    def _load_local_media(self):
        if self.original_file_path and os.path.isfile(self.original_file_path):
            self.player.setSource(QUrl.fromLocalFile(self.original_file_path))
            self._audio_source_ready = True
            self._update_playback_loading_label()
            return

        expected_path = self.original_file_path or "No local path was saved for this job."
        missing = QLabel(f"Original media file not found.\nExpected location: {expected_path}")
        missing.setWordWrap(True)
        missing.setStyleSheet("color:#f2c94c; padding: 16px;")
        self.cards_layout.insertWidget(0, missing)
        self.locate_media_btn.setVisible(True)
        self.playback_info.setText("Locate the original media file to enable playback.")

    def on_locate_media(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate original media", self.original_file_path or "",
            "Media Files (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.mp4 *.mov *.mkv *.avi)",
        )
        if not path:
            return
        self.original_file_path = path
        QSettings("TranscribeApp", "TranscribeApp").setValue(f"local_media/{self.job_id}", path)
        self.locate_media_btn.setVisible(False)
        self.player.setSource(QUrl.fromLocalFile(path))
        self._audio_source_ready = True
        self._update_playback_loading_label()

    def _apply_playback_words(self, raw_words):
        if not raw_words:
            empty = QLabel(
                "Word-level timestamps aren't available for this job (it may have been processed "
                "before this feature was added, or the provider used doesn't supply word timing)."
            )
            empty.setWordWrap(True)
            empty.setStyleSheet("color:#8b93a3; padding: 20px;")
            self.cards_layout.insertWidget(0, empty)
        else:
            self.words = raw_words
            self.word_starts = [float(word["start"]) for word in self.words]
            self._prepare_card_groups()
        self._update_playback_loading_label()

    def _update_playback_loading_label(self):
        if getattr(self, "_audio_source_ready", False):
            self.playback_info.setText("Speaker: —  |  Word: —")
        else:
            self.playback_info.setText("Locate the original media file to enable playback.")

    def _prepare_card_groups(self):
        """Group words once, then create a bounded number of widgets per tick."""
        current_speaker = None
        current_group = []
        for w in self.words:
            if w["speaker"] != current_speaker:
                if current_group:
                    self._pending_card_groups.append((current_speaker, current_group))
                current_speaker = w["speaker"]
                current_group = []
            current_group.append(w)
        if current_group:
            self._pending_card_groups.append((current_speaker, current_group))
        self._card_build_timer.start(0)

    def _build_next_card_batch(self):
        # 20 cards bounds one event-loop turn, retaining speaker-card UX while
        # keeping scrolling, painting, and window messages responsive.
        for _ in range(min(20, len(self._pending_card_groups))):
            speaker, words = self._pending_card_groups.pop(0)
            card = SpeakerCard(speaker, words, self.on_word_clicked)
            self.cards.append(card)
            for word in words:
                self.card_by_word_index[word["global_index"]] = card
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        if not self._pending_card_groups:
            self._card_build_timer.stop()

    def on_word_clicked(self, global_index: int):
        start_ms = int(self.words[global_index]["start"] * 1000)
        self.player.setPosition(start_ms)
        self.player.play()

    def on_search_changed(self, term: str):
        self._search_term = term
        self._search_card_index = 0
        self._search_timer.start(150)

    def _apply_search_batch(self):
        end = min(self._search_card_index + 20, len(self.cards))
        for card in self.cards[self._search_card_index:end]:
            card.set_search_term(self._search_term)
        self._search_card_index = end
        if self._search_card_index < len(self.cards):
            self._search_timer.start(0)

    def on_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")

    def seek_relative(self, seconds: int):
        new_pos = max(0, self.player.position() + seconds * 1000)
        self.player.setPosition(new_pos)

    def on_slider_moved(self, value):
        self.player.setPosition(value)

    def on_speed_changed(self, text: str):
        rate = float(text.replace("x", ""))
        self.player.setPlaybackRate(rate)

    def on_volume_changed(self, value: int):
        self.audio_output.setVolume(value / 100.0)

    def on_duration_changed(self, duration: int):
        self.seek_slider.setRange(0, duration)

    def on_position_changed(self, position_ms: int):
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(position_ms)
        self.seek_slider.blockSignals(False)

        total = self.player.duration()
        self.time_label.setText(f"{self._fmt(position_ms)} / {self._fmt(total)}")

        if not self.words:
            return
        t = position_ms / 1000.0
        idx = self._find_active_word(t)
        if idx != self.active_index:
            previous_card = self.active_card
            self.active_index = idx
            current_card = self.card_by_word_index.get(idx)
            if previous_card and previous_card is not current_card:
                previous_card.set_active_word(-1)
            if current_card:
                current_card.set_active_word(idx)
                self.scroll_area.ensureWidgetVisible(current_card, ymargin=40)
            self.active_card = current_card
            if idx >= 0:
                word = self.words[idx]
                self.playback_info.setText(f"Speaker: {word['speaker']}  |  Word: {word['word']}")
            else:
                self.playback_info.setText("Speaker: —  |  Word: —")

    def _find_active_word(self, t: float) -> int:
        return active_word_index(self.words, t, self.word_starts)

    @staticmethod
    def _is_valid_word(word: dict) -> bool:
        try:
            return bool(str(word.get("word", "")).strip()) and float(word["start"]) >= 0 and float(word["end"]) >= float(word["start"])
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not self.search_input.hasFocus():
            self.on_play_pause()
            event.accept()
            return
        if event.key() == Qt.Key_Left and not self.search_input.hasFocus():
            self.seek_relative(-5)
            event.accept()
            return
        if event.key() == Qt.Key_Right and not self.search_input.hasFocus():
            self.seek_relative(5)
            event.accept()
            return
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.search_input.setFocus()
            self.search_input.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _fmt(ms: int) -> str:
        if ms <= 0:
            return "0:00"
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"

    def closeEvent(self, event):
        self.player.stop()
        if self._temp_audio_path and os.path.exists(self._temp_audio_path):
            try:
                os.remove(self._temp_audio_path)
            except Exception:
                pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
