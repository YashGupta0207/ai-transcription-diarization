"""
Central registry mapping Provider.adapter_key (a.k.a. "provider_type" in the
UI) -> adapter instance. Adding a specialized provider = write the adapter
class, register it here. Providers whose type doesn't match anything
registered here still work, via GenericAdapter (see get_or_generic).
"""
from app.adapters.base import BaseProviderAdapter
from app.adapters.openai_adapter import OpenAIAdapter
from app.adapters.azure_openai_adapter import AzureOpenAIAdapter
from app.adapters.gemini_adapter import GeminiAdapter
from app.adapters.azure_speech_adapter import AzureSpeechAdapter
from app.adapters.generic_adapter import GenericAdapter


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, BaseProviderAdapter] = {}

    def register(self, adapter: BaseProviderAdapter) -> None:
        self._adapters[adapter.key] = adapter

    def get(self, key: str) -> BaseProviderAdapter:
        adapter = self._adapters.get(key)
        if adapter is None:
            raise KeyError(f"No adapter registered for key '{key}'")
        return adapter

    def get_or_generic(self, key: str) -> BaseProviderAdapter:
        """
        Used by the Gateway at request time. A provider_type that isn't one
        of the specialized adapters below is NOT an error — it's exactly
        the "any future provider, no code change" case, handled by the
        generic REST passthrough adapter.
        """
        return self._adapters.get(key) or self._adapters[GenericAdapter.key]

    def all(self) -> dict[str, BaseProviderAdapter]:
        return dict(self._adapters)


registry = AdapterRegistry()
registry.register(OpenAIAdapter())
registry.register(AzureOpenAIAdapter())
registry.register(GeminiAdapter())
registry.register(AzureSpeechAdapter())
registry.register(GenericAdapter())
