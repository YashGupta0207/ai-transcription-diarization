"""Unit tests for pure utility functions (no DB/network required)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.utils.media import (
    is_supported, is_video, format_srt_timestamp, format_vtt_timestamp,
    segments_to_srt, segments_to_readable
)


def test_is_supported():
    assert is_supported("audio.mp3") is True
    assert is_supported("video.mkv") is True
    assert is_supported("doc.pdf") is False


def test_is_video():
    assert is_video("clip.mp4") is True
    assert is_video("song.wav") is False


def test_srt_timestamp_format():
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(65.5) == "00:01:05,500"


def test_vtt_timestamp_format():
    assert format_vtt_timestamp(3661.25) == "01:01:01.250"


def test_segments_to_srt():
    segs = [{"speaker": "Speaker 1", "start": 0.0, "end": 1.5, "text": "Hi"}]
    out = segments_to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\nSpeaker 1: Hi" in out


def test_segments_to_readable_groups_same_speaker():
    segs = [
        {"speaker": "Speaker 1", "start": 0, "end": 1, "text": "Hello"},
        {"speaker": "Speaker 1", "start": 1, "end": 2, "text": "there"},
        {"speaker": "Speaker 2", "start": 2, "end": 3, "text": "Hi"},
    ]
    out = segments_to_readable(segs)
    assert out == "Speaker 1\n\nHello there\n\nSpeaker 2\n\nHi"
