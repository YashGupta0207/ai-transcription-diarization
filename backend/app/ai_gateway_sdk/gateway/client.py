import os
import httpx
from typing import Any, Dict, Optional, Generator, Union

class GatewayError(Exception):
    def __init__(self, status_code: int, response_body: str):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Gateway Error {status_code}: {response_body}")

class GatewayClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DXAI_API_KEY")
        self.base_url = (base_url or os.environ.get("DXAI_BASE_URL") or "http://localhost:8000").rstrip("/")
        
    def _get_client(self, api_key: Optional[str] = None) -> httpx.Client:
        key = api_key or self.api_key
        if not key:
            raise ValueError("API key must be provided or set in DXAI_API_KEY environment variable")
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {key}"}
        )
        
    def _request(self, method: str, path: str, provider: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers["X-Gateway-Provider"] = provider
        if profile:
            headers["X-Gateway-Profile"] = profile
        if tags:
            import json
            headers["X-Gateway-Tags"] = json.dumps(tags)
        
        client = self._get_client(api_key)
        response = client.request(method, f"/api/v1/gateway{path}", headers=headers, **kwargs)
        if not response.is_success:
            raise GatewayError(response.status_code, response.text)
        return response

    def _stream(self, method: str, path: str, provider: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, **kwargs) -> Generator[bytes, None, None]:
        headers = kwargs.pop("headers", {})
        headers["X-Gateway-Provider"] = provider
        if profile:
            headers["X-Gateway-Profile"] = profile
        if tags:
            import json
            headers["X-Gateway-Tags"] = json.dumps(tags)
        
        client = self._get_client(api_key)
        with client.stream(method, f"/api/v1/gateway{path}", headers=headers, **kwargs) as response:
            if not response.is_success:
                response.read()
                raise GatewayError(response.status_code, response.text)
            for chunk in response.iter_bytes():
                yield chunk

    def chat(self, provider: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, prompt: Optional[str] = None, messages: Optional[list[dict]] = None, model: Optional[str] = None, stream: bool = False, **kwargs) -> Any:
        if prompt and not messages:
            messages = [{"role": "user", "content": prompt}]
            
        payload = {"messages": messages}
        if model:
            payload["model"] = model
        payload.update(kwargs)
        
        if stream:
            payload["stream"] = True
            return self._stream("POST", "/chat/completions", provider, api_key=api_key, profile=profile, tags=tags, json=payload)
        
        response = self._request("POST", "/chat/completions", provider, api_key=api_key, profile=profile, tags=tags, json=payload)
        return response.json()

    def transcribe(self, provider: str, file: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, mimetype: str = "audio/wav", **kwargs) -> Any:
        with open(file, "rb") as f:
            content = f.read()
            
        headers = {"Content-Type": mimetype}
        response = self._request("POST", "/listen", provider, api_key=api_key, profile=profile, tags=tags, content=content, headers=headers, params=kwargs)
        return response.json()
        
    def query(self, provider: str, query: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, **kwargs) -> Any:
        payload = {"query": query}
        payload.update(kwargs)
        response = self._request("POST", "/query", provider, api_key=api_key, profile=profile, tags=tags, json=payload)
        return response.json()
        
    def request(self, provider: str, method: str, path: str, api_key: Optional[str] = None, profile: Optional[str] = None, tags: Optional[dict] = None, **kwargs) -> Any:
        response = self._request(method, path, provider, api_key=api_key, profile=profile, tags=tags, **kwargs)
        try:
            return response.json()
        except ValueError:
            return response.content

# Default instance for module-level usage
default_client = GatewayClient()

def chat(*args, **kwargs):
    return default_client.chat(*args, **kwargs)

def transcribe(*args, **kwargs):
    return default_client.transcribe(*args, **kwargs)

def query(*args, **kwargs):
    return default_client.query(*args, **kwargs)

def request(*args, **kwargs):
    return default_client.request(*args, **kwargs)
