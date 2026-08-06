# Gateway SDK

Developer SDK for the AI Gateway. You authenticate with a `dev_xxxxx` token issued from the admin portal — you never see or handle a real provider API key, endpoint, or secret. Every call is routed through the Gateway, which resolves your token to whichever provider you request.

## Install

```bash
pip install -e .
```

## Usage

The SDK exposes a single `gateway` module that handles all requests. You must specify the `provider` name exactly as it appears in the Admin Portal.

```python
import gateway

# Chat completions (OpenAI / Azure OpenAI / Gemini style)
response = gateway.chat(
    api_key="dev_xxxxxxxxx",
    provider="Azure OpenAI",
    model="gpt-4o",
    prompt="Hello!",
)
print(response)

# Streaming
for chunk in gateway.chat(
    api_key="dev_xxxxxxxxx",
    provider="Azure OpenAI",
    prompt="Stream this",
    stream=True,
):
    print(chunk)

# Transcription (Deepgram style)
result = gateway.transcribe(
    api_key="dev_xxxxxxxxx",
    provider="Deepgram",
    file="audio.wav",
    mimetype="audio/wav"
)
print(result)
```

## Configuration

Instead of passing `api_key` explicitly, you can set environment variables:

```bash
export DXAI_API_KEY=dev_xxxxxxxxx
export DXAI_BASE_URL=https://your-gateway.example.com
```

Then you can omit the `api_key` parameter:

```python
import gateway

response = gateway.chat(
    provider="Azure OpenAI",
    prompt="Hello!"
)
```

## Error handling

```python
import gateway
from gateway import GatewayError

try:
    gateway.chat(provider="Azure OpenAI", prompt="Hello")
except GatewayError as e:
    print(e.status_code, e.response_body)
```
