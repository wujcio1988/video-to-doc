"""Testy regresyjne naprawy enrich LLM (R-A..R-F), 2026-09-27.

Zakres:
  R-A  odzyskanie treści z ``reasoning_content`` przy pustym ``content``.
  R-B  jawna diagnostyka: pusta vs nieparsowalna odpowiedź (nie ciche ``{}``).
  R-C  ``max_tokens`` enrichera == 8000.
  R-D  dokładnie 1 automatyczny retry przy None/pustym wyniku.
  R-E  domyślny ``LLMConfig().enricher_model``.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from vtd.config import LLMConfig
from vtd.core import enricher
from vtd.core.fusion import ManualStep
from vtd.providers.openai_client import OpenAIProvider, robust_json_decode


# ---------------------------------------------------------------------------
# R-A: pusty content + reasoning_content z JSON -> odzysk
# ---------------------------------------------------------------------------

def _provider_with_message(message: dict) -> OpenAIProvider:
    provider = OpenAIProvider(base_url="https://example.test/v1", api_key="k")
    fake_resp = {
        "choices": [{"message": message}],
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    return provider, mock_resp


def test_r_a_recovers_json_from_reasoning_content():
    json_payload = '{"meta": {}, "intro": "Cel.", "steps": [{"step_number": 1}]}'
    provider, mock_resp = _provider_with_message(
        {"content": "", "reasoning_content": f"Analizuję nagranie...\n{json_payload}\nKoniec."}
    )
    with patch("urllib.request.urlopen", return_value=mock_resp) as m:
        res = provider.chat_completion(messages=[{"role": "user", "content": "x"}], model="m", json_mode=True)
    assert res.strip() == json_payload
    assert json.loads(res)["steps"][0]["step_number"] == 1
    assert m.called


def test_r_a_empty_content_no_reasoning_raises_explicit():
    """R-A zdanie 2: nigdy nie zwracaj pustego stringa bez śladu -> jawny ValueError."""
    provider, mock_resp = _provider_with_message({"content": "", "reasoning_content": ""})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ValueError, match="pustą treść"):
            provider.chat_completion(messages=[{"role": "user", "content": "x"}], model="m", json_mode=True)


# ---------------------------------------------------------------------------
# R-B: pusta vs nieparsowalna odpowiedź (nie ciche {})
# ---------------------------------------------------------------------------

def test_r_b_empty_text_raises_not_silent_empty_dict():
    with pytest.raises(ValueError, match="Pusta odpowiedź"):
        robust_json_decode("")
    with pytest.raises(ValueError, match="Pusta odpowiedź"):
        robust_json_decode("   ")


def test_r_b_unparsable_includes_excerpt_and_enricher_logs(tmp_path, capsys):
    with pytest.raises(ValueError, match="Nie udało się sparsować"):
        robust_json_decode("to nie jest json")

    class _GarbageProvider:
        calls = 0

        def chat_completion(self, messages, **kwargs):
            type(self).calls += 1
            return "totalnie nieparsowalna odpowiedź modelu " * 20

    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    step = ManualStep(step_number=1, start_time=0.0, end_time=10.0, frame_path=frame, speech_text="Krok")

    with patch.object(enricher, "load_config"), \
         patch.object(enricher, "get_llm_provider", return_value=_GarbageProvider()):
        result = enricher.enrich_steps_with_llm(title="T", steps=[step], model="m")
    out = capsys.readouterr().out
    assert result is None
    assert "błąd parsowania JSON" in out
    assert "Nie udało się sparsować" in out


# ---------------------------------------------------------------------------
# R-D: retry przy pierwszym None / pustym wyniku
# ---------------------------------------------------------------------------

def test_r_d_retries_once_on_first_none(tmp_path):
    valid = '{"meta": {}, "steps": [{"step_number": 1, "title": "T"}]}'

    class _FlakyProvider:
        calls = 0

        def chat_completion(self, messages, **kwargs):
            type(self).calls += 1
            return None if type(self).calls == 1 else valid

    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    step = ManualStep(step_number=1, start_time=0.0, end_time=10.0, frame_path=frame, speech_text="Krok")

    with patch.object(enricher, "load_config"), \
         patch.object(enricher, "get_llm_provider", return_value=_FlakyProvider()):
        result = enricher.enrich_steps_with_llm(title="T", steps=[step], model="m")

    assert _FlakyProvider.calls == 2
    assert result is not None
    assert result["steps"][0]["step_number"] == 1


# ---------------------------------------------------------------------------
# R-C + R-E: budżet tokenów i domyślny model enrichera
# ---------------------------------------------------------------------------

def test_r_c_enricher_max_tokens_is_8000(tmp_path):
    captured = {}

    class _CaptureKwargs:
        def chat_completion(self, messages, **kwargs):
            captured.update(kwargs)
            return '{"meta": {}, "steps": []}'

    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    step = ManualStep(step_number=1, start_time=0.0, end_time=10.0, frame_path=frame, speech_text="Krok")

    with patch.object(enricher, "load_config"), \
         patch.object(enricher, "get_llm_provider", return_value=_CaptureKwargs()):
        enricher.enrich_steps_with_llm(title="T", steps=[step], model="m")

    assert captured.get("max_tokens") == 8000


def test_r_e_default_enricher_model():
    assert LLMConfig().enricher_model == "opencode-go/minimax-m3"
    # G-1: default CLI (vtd.defaults) musi być zgodny z configiem — inaczej
    # ścieżka CLI nadpisuje default i wzbogacanie trafia do modelu z innego źródła.
    from vtd.defaults import DEFAULT_ENRICH_MODEL
    assert DEFAULT_ENRICH_MODEL == "opencode-go/minimax-m3"
