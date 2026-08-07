from starlette.requests import Request

from app.adapters.base import BaseProviderAdapter, BuiltRequest, CredentialField


class AzureSpeechAdapter(BaseProviderAdapter):
    key = "azure_speech"
    display_name = "Azure Speech"

    @classmethod
    def credential_schema(cls) -> list[CredentialField]:
        return [
            CredentialField(
                name="api_key", label="API Key", field_type="secret",
                required=True, is_mandatory_primary=True,
                placeholder="Azure Speech API key",
            ),
            CredentialField(
                name="region", label="Region", field_type="text",
                required=True, is_mandatory_primary=False,
                placeholder="e.g., eastus",
            ),
        ]

    async def build_request(self, *, incoming: Request, path: str, body: bytes, credentials: dict[str, str]) -> BuiltRequest:
        region = self.credential_value(credentials, 'region', 'azure_region')
        api_key = self.credential_value(credentials, 'api_key', 'azure_key', 'azure_speech_key')
        
        base_url = f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
        
        content_type = incoming.headers.get("content-type", "audio/wav")
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        
        # Azure requires language parameter, default to en-US if not provided
        params = dict(incoming.query_params)
        if "language" not in params:
            params["language"] = "en-US"
            
        return BuiltRequest(
            method=incoming.method,
            url=base_url,
            headers=headers,
            params=params,
            content=body,
            is_streaming=False,
        )

    def build_websocket_request(self, *, incoming: Request, path: str, credentials: dict[str, str]) -> tuple[str, dict[str, str]]:
        region = self.credential_value(credentials, 'region', 'azure_region')
        api_key = self.credential_value(credentials, 'api_key', 'azure_key', 'azure_speech_key')
        
        # Azure Speech WebSocket URL
        url = f"wss://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
        
        # Append query params from incoming request
        query_string = incoming.url.query
        if query_string:
            url = f"{url}?{query_string}"
            if "language=" not in url:
                url += "&language=en-US"
        else:
            url += "?language=en-US"
            
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
        }
        return url, headers

    def normalize_usage(self, content: bytes) -> dict[str, int | float]:
        import json
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            return super().normalize_usage(content)
        
        # Azure Speech REST API returns Duration in 100-nanosecond units
        duration_ticks = payload.get("Duration", 0)
        duration_seconds = duration_ticks / 10_000_000
        
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": int(duration_seconds), "estimated_cost": 0.0}
