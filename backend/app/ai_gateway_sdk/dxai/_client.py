"""
Internal HTTP client. Developers never see this directly — it's what
`chat.completions.create(...)` etc. call underneath. Automatically attaches
the developer token as a Bearer header and always talks to OUR gateway,
never the real provider — the developer's `dev_xxx` key is all it needs.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx


class DXAIError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class _BaseGatewayClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float = 120.0):
        self.api_key = api_key or os.environ.get("DXAI_API_KEY")
        if not self.api_key:
            raise DXAIError(
                "No API key provided. Pass api_key='dev_...' or set the DXAI_API_KEY env var. "
                "Get a token from your admin portal — never a real provider key."
            )
        self.base_url = (base_url or os.environ.get("DXAI_BASE_URL") or "https://gateway.yourdomain.com").rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
        )

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 files: dict | None = None, params: dict | None = None, provider: str | None = None,
                 content: bytes | None = None) -> dict:
        headers = {"X-Gateway-Provider": provider} if provider else None
        response = self._http.request(method, f"/gateway{path}", json=json_body, files=files, params=params, headers=headers, content=content)
        if response.status_code >= 400:
            raise DXAIError(
                f"Gateway request failed with status {response.status_code}",
                status_code=response.status_code, response_body=response.text,
            )
        return response.json()

    def _stream(self, method: str, path: str, *, json_body: dict | None = None) -> Iterator[dict]:
        with self._http.stream(method, f"/gateway{path}", json=json_body) as response:
            if response.status_code >= 400:
                response.read()
                raise DXAIError(
                    f"Gateway request failed with status {response.status_code}",
                    status_code=response.status_code, response_body=response.text,
                )
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    def close(self) -> None:
        self._http.close()
