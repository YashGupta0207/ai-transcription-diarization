"""
Minimal PySide6 desktop client.

Design intent (per project requirements): NO local transcription/diarization,
NO fancy UI. The app only uploads, polls, and displays results. It is safe
to close this app entirely mid-job - the backend/worker keep processing
independently, and reopening the app just resumes polling via GET /jobs.
"""
import sys
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
    QLabel, QMessageBox, QComboBox
)

from client import BackendClient

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

    # ---------------- Actions ----------------

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
            return  # backend unreachable - silently retry on next tick

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


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
