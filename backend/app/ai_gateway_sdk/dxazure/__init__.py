"""
Azure Speech SDK, separate package so it matches the shape developers
might expect for Azure Speech services.

    from dxazure import AzureSpeechClient

    client = AzureSpeechClient(api_key="dev_xxxxx")
    
    # For WebSocket live transcription:
    async with client.listen() as ws:
        await ws.send_bytes(audio_chunk)
        transcript = await ws.receive_json()

Internally this still just calls OUR gateway with the dev_ token.
"""
from __future__ import annotations

import os
import json
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import websockets


class AzureSpeechClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AzureSpeechClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("DXAI_API_KEY")
        if not self.api_key:
            raise AzureSpeechClientError(
                "No API key provided. Pass api_key='dev_...' or set DXAI_API_KEY. "
                "This is a Gateway developer token, never a real Azure key."
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
            headers={
                "Content-Type": mimetype,
                "X-Gateway-Provider": "azure_speech",
            },
            params=params,
        )
        if response.status_code >= 400:
            raise AzureSpeechClientError(
                f"Gateway request failed with status {response.status_code}",
                status_code=response.status_code, response_body=response.text,
            )
        return response.json()

    @asynccontextmanager
    async def listen(self, **params: Any):
        """
        Open a WebSocket connection to the gateway for live transcription.
        """
        ws_base = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_base}/api/v1/gateway/ws/listen?token={self.api_key}&provider=azure_speech"
        
        for k, v in params.items():
            ws_url += f"&{k}={v}"
            
        try:
            async with websockets.connect(ws_url) as ws:
                yield _WebSocketWrapper(ws)
        except Exception as e:
            raise AzureSpeechClientError(f"WebSocket connection failed: {e}")

    def close(self) -> None:
        self._http.close()


class _WebSocketWrapper:
    def __init__(self, ws):
        self._ws = ws
        
    async def send_bytes(self, data: bytes):
        await self._ws.send(data)
        
    async def send_text(self, data: str):
        await self._ws.send(data)
        
    async def receive_json(self) -> dict:
        message = await self._ws.recv()
        if isinstance(message, bytes):
            return json.loads(message.decode('utf-8'))
        return json.loads(message)
        
    async def receive_text(self) -> str:
        message = await self._ws.recv()
        if isinstance(message, bytes):
            return message.decode('utf-8')
        return message

__all__ = ["AzureSpeechClient", "AzureSpeechClientError"]
