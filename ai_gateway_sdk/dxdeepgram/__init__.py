"""
Deepgram-flavored SDK, separate package so it matches the shape developers
already expect from Deepgram's own SDK:

    from dxdeepgram import DeepgramClient

    client = DeepgramClient(api_key="dev_xxxxx")
    result = client.transcribe_file("audio.wav", mimetype="audio/wav")

Internally this still just calls OUR gateway with the dev_ token — same
gateway, same token/provider validation, same encrypted-credential flow as
the dxai package. It's a thin, provider-shaped wrapper for ergonomics.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class DeepgramClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class DeepgramClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("DXAI_API_KEY")
        if not self.api_key:
            raise DeepgramClientError(
                "No API key provided. Pass api_key='dev_...' or set DXAI_API_KEY. "
                "This is a Gateway developer token, never a real Deepgram key."
            )
        self.base_url = (base_url or os.environ.get("DXAI_BASE_URL") or "https://gateway.yourdomain.com").rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
        )

    def transcribe_file(self, file_path: str, *, mimetype: str = "audio/wav", **params: Any) -> dict:
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        return self.transcribe_bytes(audio_bytes, mimetype=mimetype, **params)

    def transcribe_bytes(self, audio_bytes: bytes, *, mimetype: str = "audio/wav", **params: Any) -> dict:
        response = self._http.post(
            "/gateway/listen",
            content=audio_bytes,
            headers={"Content-Type": mimetype},
            params=params,
        )
        if response.status_code >= 400:
            raise DeepgramClientError(
                f"Gateway request failed with status {response.status_code}",
                status_code=response.status_code, response_body=response.text,
            )
        return response.json()

    def close(self) -> None:
        self._http.close()


__all__ = ["DeepgramClient", "DeepgramClientError"]
