"""Media utility helpers: extension checks, ffmpeg audio extraction, export formats."""
import os
import subprocess
from typing import List

from app.config import settings


def is_supported(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in settings.SUPPORTED_EXTENSIONS


def is_video(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in settings.VIDEO_EXTENSIONS


def extract_audio(input_path: str, output_path: str) -> str:
    """
    Extract mono 16kHz WAV audio from a video (or re-encode audio) using ffmpeg.
    Requires the `ffmpeg` binary to be present on the worker's system/container.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-2000:]}")
    return output_path


def format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def segments_to_srt(segments: List[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{format_srt_timestamp(seg['start'])} --> {format_srt_timestamp(seg['end'])}")
        lines.append(f"{seg['speaker']}: {seg['text']}")
        lines.append("")
    return "\n".join(lines)


def segments_to_vtt(segments: List[dict]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{format_vtt_timestamp(seg['start'])} --> {format_vtt_timestamp(seg['end'])}")
        lines.append(f"{seg['speaker']}: {seg['text']}")
        lines.append("")
    return "\n".join(lines)


def segments_to_readable(segments: List[dict]) -> str:
    """Groups consecutive same-speaker lines into the 'Speaker N \\n text' block format."""
    blocks = []
    current_speaker = None
    current_lines = []
    for seg in segments:
        if seg["speaker"] != current_speaker:
            if current_speaker is not None:
                blocks.append(f"{current_speaker}\n\n" + " ".join(current_lines))
            current_speaker = seg["speaker"]
            current_lines = [seg["text"]]
        else:
            current_lines.append(seg["text"])
    if current_speaker is not None:
        blocks.append(f"{current_speaker}\n\n" + " ".join(current_lines))
    return "\n\n".join(blocks)
