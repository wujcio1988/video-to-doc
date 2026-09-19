import json
import io
import urllib.request
from unittest.mock import MagicMock, patch
from pathlib import Path
from PIL import Image

from vtd.providers.openai_client import OpenAIProvider, robust_json_decode
from vtd.providers.factory import get_llm_provider
from vtd.config import LLMConfig


def test_robust_json_decode():
    # Direct JSON
    assert robust_json_decode('{"key": "value"}') == {"key": "value"}
    
    # Markdown block
    md = 'Odpowiedź:\n```json\n{"status": "ok"}\n```\nKoniec'
    assert robust_json_decode(md) == {"status": "ok"}

    # Mixed text
    mixed = 'Tekst przed {"items": [1, 2, 3]} tekst po'
    assert robust_json_decode(mixed) == {"items": [1, 2, 3]}


def test_openai_provider_chat():
    provider = OpenAIProvider(base_url="https://api.openai.com/v1", api_key="test-key")
    
    fake_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '{"verdict": "ok"}'
                }
            }
        ]
    }
    
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = provider.chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o-mini",
            json_mode=True
        )
        assert res == '{"verdict": "ok"}'
        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.openai.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer test-key"


def test_openai_provider_vision(tmp_path):
    provider = OpenAIProvider(base_url="https://api.openai.com/v1", api_key="test-key")
    
    img_path = tmp_path / "test.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(img_path)

    fake_resp = {
        "choices": [{"message": {"content": "vision response"}}]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = provider.vision_completion("Opisz ten obraz", img_path, model="gpt-4o-mini")
        assert res == "vision response"
