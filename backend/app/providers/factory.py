"""Single switch point: change SPEECH_PROVIDER in .env to swap vendors app-wide."""
from app.config import settings
from app.providers.base import SpeechProvider


def get_provider(name: str = None) -> SpeechProvider:
    provider_name = (name or settings.SPEECH_PROVIDER).lower()

    if settings.SPEECH_PROVIDER == "gateway":
        from app.providers.gateway_provider import GatewayProvider
        target = provider_name if provider_name != "gateway" else settings.GATEWAY_TARGET_PROVIDER
        return GatewayProvider(target_provider=target)

    if provider_name == "gateway":
        from app.providers.gateway_provider import GatewayProvider
        return GatewayProvider(target_provider=settings.GATEWAY_TARGET_PROVIDER)
    if provider_name == "azure":
        from app.providers.azure_provider import AzureProvider
        return AzureProvider()
    if provider_name == "deepgram":
        from app.providers.deepgram_provider import DeepgramProvider
        return DeepgramProvider()
    if provider_name == "whisper":
        from app.providers.whisper_provider import WhisperProvider
        return WhisperProvider()
    if provider_name == "openrouter":
        from app.providers.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider()
    if provider_name == "assemblyai":
        from app.providers.assemblyai_provider import AssemblyAIProvider
        return AssemblyAIProvider()
    if provider_name == "gladia":
        from app.providers.gladia_provider import GladiaProvider
        return GladiaProvider()

    raise ValueError(f"Unknown SPEECH_PROVIDER: {provider_name}")
