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

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
    QLabel, QMessageBox, QComboBox, QDialog, QSlider, QScrollArea, QFrame, QLineEdit
)

from client import BackendClient
from live_transcriber import LiveTranscriber
from transcript_sync import active_word_index, format_timestamp

POLL_INTERVAL_MS = 4000


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TranscribeApp Desktop")
        self.resize(900, 600)

        self.client = BackendClient()
        self.jobs_by_row = {}

        root = QWidget()
        layout = QHBoxLayout(root)

        # ---- Left panel: job list + controls ----
        left = QVBoxLayout()
        self.upload_btn = QPushButton("Upload Audio/Video File")
        self.upload_btn.clicked.connect(self.on_upload)
        left.addWidget(self.upload_btn)

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
        try:
            result = self.client.upload_file(path)
            QMessageBox.information(self, "Uploaded", f"Job queued: {result['job_id']}")
            self.refresh_jobs()
        except Exception as e:
            QMessageBox.critical(self, "Upload failed", str(e))

    def refresh_jobs(self):
        try:
            data = self.client.list_jobs()
        except Exception:
            return

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
        self.status_label.setText(
            f"Job {job['id']}  |  Status: {job['status']}  |  Provider: {job['provider']}"
        )
        self.playback_btn.setEnabled(job["status"] == "completed")
        if job["status"] == "completed":
            try:
                result = self.client.get_result(job["id"])
                self.transcript_view.setPlainText(result.get("readable_transcript") or "(empty transcript)")
            except Exception as e:
                self.transcript_view.setPlainText(f"Error loading transcript: {e}")
        elif job["status"] == "failed":
            self.transcript_view.setPlainText(f"Job failed: {job.get('error_message', 'unknown error')}")
        else:
            self.transcript_view.setPlainText("Processing on the server - this window can be closed safely.")

    def on_cancel(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        try:
            self.client.cancel(job_id)
            self.refresh_jobs()
        except Exception as e:
            QMessageBox.critical(self, "Cancel failed", str(e))

    def on_delete(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        try:
            self.client.delete(job_id)
            self.refresh_jobs()
            self.transcript_view.clear()
        except Exception as e:
            QMessageBox.critical(self, "Delete failed", str(e))

    def on_export(self):
        job_id = self.current_job_id()
        if not job_id:
            return
        fmt = self.export_format.currentText()
        try:
            content = self.client.export(job_id, fmt)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "Save export", f"transcript.{fmt}")
        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, "Saved", f"Exported to {save_path}")

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
        dlg = PlaybackDialog(self.client, job_id, filename, self)
        dlg.exec()


class LiveTranscriptionDialog(QDialog):
    def __init__(self, client: BackendClient, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Transcription")
        self.resize(600, 500)
        self.client = client
        self.transcriber = None

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Not recording")
        layout.addWidget(self.status_label)

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

    def on_start(self):
        try:
            self.transcriber = LiveTranscriber(self.client.live_ws_url())
            self.transcriber.transcript_received.connect(self.on_transcript)
            self.transcriber.saved.connect(self.on_saved)
            self.transcriber.error.connect(self.on_error)
            self.transcriber.stopped.connect(self.on_stopped)
            self.transcriber.start()
            self.status_label.setText("Recording…")
            self.start_btn.setEnabled(False)
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
        if not data.get("is_final"):
            return
        if data["speaker"] != self._current_speaker:
            self.transcript_view.append(f"\n{data['speaker']}\n")
            self._current_speaker = data["speaker"]
        self.transcript_view.insertPlainText(data["text"] + " ")

    def on_saved(self, job_id):
        if job_id:
            QMessageBox.information(self, "Saved", "Live session saved to your job list.")
        self.status_label.setText("Not recording")
        self.start_btn.setEnabled(True)

    def on_error(self, message: str):
        QMessageBox.critical(self, "Live transcription error", message)

    def on_stopped(self):
        self.status_label.setText("Not recording")
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)


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
        contains = any(w["global_index"] == global_index for w in self.words)
        if self.active_global_index != global_index:
            self.active_global_index = global_index if contains else -1
            self.render_html()
        if self._active != contains:
            self._active = contains
            self._apply_style()
        return contains


class PlaybackDialog(QDialog):
    def __init__(self, client: BackendClient, job_id: str, filename: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verify Playback")
        self.resize(760, 620)
        self.client = client
        self.job_id = job_id
        self.words = []
        self.cards = []
        self.card_by_word_index = {}
        self.active_card = None
        self.active_index = -1
        self._temp_audio_path = None

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel(f"Verify Playback — {filename}")
        title.setStyleSheet("font-weight:700; font-size:15px;")
        header.addWidget(title)
        header.addStretch()
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
        try:
            raw_words = self.client.get_words(self.job_id)
        except Exception:
            raw_words = []

        # API data is persisted timestamp data, but a malformed legacy
        # record must not make the verifier unusable.
        raw_words = [w for w in raw_words if self._is_valid_word(w)]

        if not raw_words:
            empty = QLabel(
                "Word-level timestamps aren't available for this job (it may have been processed "
                "before this feature was added, or the provider used doesn't supply word timing)."
            )
            empty.setWordWrap(True)
            empty.setStyleSheet("color:#8b93a3; padding: 20px;")
            self.cards_layout.insertWidget(0, empty)
        else:
            raw_words.sort(key=lambda word: float(word["start"]))
            for idx, w in enumerate(raw_words):
                w["global_index"] = idx
            self.words = raw_words
            self._build_cards()

        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".audio")
            tmp.close()
            self._temp_audio_path = tmp.name
            self.client.download_audio(self.job_id, self._temp_audio_path)
            self.player.setSource(QUrl.fromLocalFile(self._temp_audio_path))
        except Exception as e:
            QMessageBox.warning(self, "Audio unavailable", f"Could not load audio: {e}")

    def _build_cards(self):
        """Groups consecutive same-speaker words into one card each, matching
        the reference design's one-card-per-speaker-turn layout."""
        current_speaker = None
        current_group = []

        def flush():
            if current_group:
                card = SpeakerCard(current_speaker, current_group, self.on_word_clicked)
                self.cards.append(card)
                for word in current_group:
                    self.card_by_word_index[word["global_index"]] = card
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        for w in self.words:
            if w["speaker"] != current_speaker:
                flush()
                current_speaker = w["speaker"]
                current_group = []
            current_group.append(w)
        flush()

    def on_word_clicked(self, global_index: int):
        start_ms = int(self.words[global_index]["start"] * 1000)
        self.player.setPosition(start_ms)
        self.player.play()

    def on_search_changed(self, term: str):
        for card in self.cards:
            card.set_search_term(term)

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
        return active_word_index(self.words, t)

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
