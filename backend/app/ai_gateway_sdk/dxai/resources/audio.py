from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .._client import _BaseGatewayClient


class Transcriptions:
    def __init__(self, client: _BaseGatewayClient) -> None:
        self._client = client

    def create(
        self,
        *,
        file: tuple[str, bytes, str],
        model: str,
        language: str | None = None,
        response_format: str | None = "verbose_json",
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """
        Transcribe audio using the AI Gateway.
        Mimics the OpenAI audio.transcriptions.create interface.
        """
        files = {"file": file}
        data = {
            "model": model,
            "response_format": response_format,
        }
        if language:
            data["language"] = language
            
        data.update(kwargs)

        return self._client._request(
            "POST",
            "/audio/transcriptions",
            files=files,
            data=data,
            provider=provider,
        )


class Audio:
    def __init__(self, client: _BaseGatewayClient) -> None:
        self.transcriptions = Transcriptions(client)
