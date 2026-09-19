"""
Fabryka providerów LLM dla Video-to-Doc.
"""
from vtd.config import LLMConfig
from vtd.providers.base import BaseLLMProvider
from vtd.providers.openai_client import OpenAIProvider, robust_json_decode

__all__ = ["BaseLLMProvider", "OpenAIProvider", "get_llm_provider", "robust_json_decode"]


def get_llm_provider(config: LLMConfig) -> BaseLLMProvider:
    """Zwraca instancję dostawcy LLM na podstawie konfiguracji."""
    return OpenAIProvider(
        base_url=config.base_url,
        api_key=config.get_api_key(),
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
