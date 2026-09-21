import os
import json
from pathlib import Path
from vtd.config import AppConfig, LLMConfig, StorageConfig, ServerConfig, load_config


def test_default_config():
    cfg = AppConfig()
    assert cfg.llm.provider == "openai"
    assert cfg.llm.base_url == "https://api.openai.com/v1"
    assert cfg.server.port == 9870
    assert cfg.pipeline.mode == "manual"


def test_api_key_resolution(monkeypatch):
    monkeypatch.setenv("TEST_CUSTOM_KEY", "secret-key-abc")
    llm = LLMConfig(api_key_env="TEST_CUSTOM_KEY")
    assert llm.get_api_key() == "secret-key-abc"

    # Direct key has priority
    llm_direct = LLMConfig(api_key="direct-val", api_key_env="TEST_CUSTOM_KEY")
    assert llm_direct.get_api_key() == "direct-val"


def test_load_config_from_json(tmp_path):
    cfg_file = tmp_path / "config.json"
    data = {
        "llm": {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "enricher_model": "deepseek/deepseek-chat"
        },
        "server": {
            "port": 8080
        }
    }
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_config(cfg_file)
    assert loaded.llm.provider == "openrouter"
    assert loaded.llm.base_url == "https://openrouter.ai/api/v1"
    assert loaded.llm.enricher_model == "deepseek/deepseek-chat"
    assert loaded.server.port == 8080


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://my-custom-proxy/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret-xyz")
    monkeypatch.setenv("OPENAI_MODEL", "qwen-custom:72b")
    monkeypatch.setenv("PORT", "9900")

    cfg = load_config()
    assert cfg.llm.base_url == "http://my-custom-proxy/v1"
    assert cfg.llm.api_key == "env-secret-xyz"
    assert cfg.llm.enricher_model == "qwen-custom:72b"
    assert cfg.llm.frame_qa_model == "qwen-custom:72b"
    assert cfg.server.port == 9900
