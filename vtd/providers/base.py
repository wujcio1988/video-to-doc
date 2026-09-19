"""
Interfejs bazowy dla dostawców modeli LLM i Vision w Video-to-Doc.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class BaseLLMProvider(ABC):
    """Abstrakcyjna klasa bazowa dla providerów AI."""

    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> str:
        """Wykonuje synchroniczne zapytanie czatu tekstowego."""
        pass

    @abstractmethod
    def vision_completion(
        self,
        prompt: str,
        image_path: Path,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 800,
        json_mode: bool = False,
    ) -> str:
        """Wykonuje zapytanie multimodalne (tekst + zrzut ekranu)."""
        pass

    @abstractmethod
    def check_health(self) -> bool:
        """Sprawdza dostępność usługi AI."""
        pass
