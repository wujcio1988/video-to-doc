"""
Klient OpenAI-compatible realizujący wywołania do dowolnego providera
(OpenAI, OpenRouter, Ollama, LM Studio, vLLM, OmniRoute itp.).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from vtd.providers.base import BaseLLMProvider


def robust_json_decode(text: str) -> Dict[str, Any]:
    """
    Tolerancyjne dekodowanie JSON ze zwróconej odpowiedzi modelu LLM.
    Wyciąga bloki ```json ... ``` lub pierwsze dopasowanie nawiasów klamrowych.
    """
    if not text:
        return {}
    clean = text.strip()

    # 1. Próba bezpośredniego parsowania
    try:
        return json.loads(clean)
    except Exception:
        pass

    # 2. Wyciągnięcie z markdown code block ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except Exception:
            pass

    # 3. Wyciągnięcie pierwszego bloku klamrowego { ... }
    first_brace = clean.find("{")
    last_brace = clean.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(clean[first_brace : last_brace + 1])
        except Exception:
            pass

    # 4. Wyciągnięcie pierwszej tablicy [ ... ]
    first_bracket = clean.find("[")
    last_bracket = clean.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        try:
            val = json.loads(clean[first_bracket : last_bracket + 1])
            return {"items": val}
        except Exception:
            pass

    raise ValueError(f"Nie udało się sparsować odpowiedzi JSON od LLM: {text[:200]}")


class OpenAIProvider(BaseLLMProvider):
    """
    Uniwersalny klient zgodny ze standardem OpenAI Chat Completions.
    Działa bez zewnętrznych bibliotek (używa wyłącznie standardowej biblioteki Python).
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

        # Formatowanie endpointu chat
        if self.base_url.endswith("/chat/completions"):
            self.chat_url = self.base_url
        else:
            self.chat_url = f"{self.base_url}/chat/completions"

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _execute_request(self, payload: Dict[str, Any]) -> str:
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = self._get_headers()

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(self.chat_url, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    choices = resp_data.get("choices", [])
                    if not choices:
                        raise ValueError(f"Brak choices w odpowiedzi LLM: {resp_data}")
                    content = choices[0].get("message", {}).get("content", "")
                    return content or ""
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                last_error = RuntimeError(f"Błąd HTTP {e.code} ({e.reason}): {err_body[:300]}")
                # 429 lub 5xx -> retry
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(1.5 ** attempt)
                    continue
                raise last_error
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    time.sleep(1.5 ** attempt)
                    continue
                raise last_error

        raise RuntimeError(f"Przekroczono limit prób wywołania LLM: {last_error}")

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._execute_request(payload)
        except urllib.error.HTTPError as e:
            # Jeśli provider nie obsługuje response_format (np. niektóre modele lokalne), ponów bez niego
            if json_mode and e.code == 400:
                payload.pop("response_format", None)
                return self._execute_request(payload)
            raise

    def vision_completion(
        self,
        prompt: str,
        image_path: Path,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 800,
        json_mode: bool = False,
    ) -> str:
        if not image_path.is_file():
            raise FileNotFoundError(f"Plik obrazu nie istnieje: {image_path}")

        mime_type, _ = mimetypes.guess_type(str(image_path))
        if not mime_type:
            mime_type = "image/png"

        img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{img_b64}"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._execute_request(payload)
        except urllib.error.HTTPError as e:
            if json_mode and e.code == 400:
                payload.pop("response_format", None)
                return self._execute_request(payload)
            raise

    def check_health(self) -> bool:
        """Sprawdza czy endpoint API odpowiada."""
        try:
            # Prosty probe endpointu models lub bazowego URL
            probe_url = self.base_url if "/models" in self.base_url else f"{self.base_url}/models"
            req = urllib.request.Request(probe_url, headers=self._get_headers())
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False
