from __future__ import annotations

from typing import Any, Iterator


class Completions:
    def __init__(self, client):
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict | Iterator[dict]:
        """
        Mirrors the official OpenAI SDK surface: client.chat.completions.create(...)
        Under the hood this hits our Gateway's /gateway/chat/completions route,
        which resolves the developer token -> assigned provider -> the right
        adapter (OpenAI or Azure OpenAI) -- the developer never knows or cares
        which one they're actually calling.
        """
        body = {"model": model, "messages": messages, "stream": stream, **kwargs}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        if stream:
            return self._client._stream("POST", "/chat/completions", json_body=body)
        return self._client._request("POST", "/chat/completions", json_body=body)


class Chat:
    def __init__(self, client):
        self.completions = Completions(client)
        self._client = client

    def __call__(self, *, provider: str, model: str, prompt: str, **kwargs: Any) -> dict:
        """Convenient provider-aware chat: gateway.chat(provider='openai', ...)."""
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], **kwargs}
        return self._client._request("POST", "/chat/completions", json_body=body, provider=provider)
