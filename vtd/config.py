"""Standalone configuration for VTD; secrets are resolved only from env."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

class LLMConfig(BaseModel):
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: Optional[str] = None
    api_key_env: str = "OPENAI_API_KEY"
    timeout: float = 60.0
    max_retries: int = 3
    enricher_model: str = "gpt-4o-mini"
    frame_qa_model: str = "gpt-4o-mini"
    element_locator_model: str = "gpt-4o-mini"
    qa_verifier_model: str = "gpt-4o-mini"
    def get_api_key(self) -> Optional[str]:
        return self.api_key or os.getenv(self.api_key_env)

class StorageConfig(BaseModel):
    recordings_dir: Path = Path("./recordings")
    output_dir: Path = Path("./output")
    data_dir: Path = Path("./data")
    reference_docs_dir: Optional[Path] = None

class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9870

class PipelineSettings(BaseModel):
    mode: str = "manual"
    whisper_model: str = "large-v3"
    whisper_language: str = "pl"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    scene_threshold: float = 0.05
    phash_threshold: int = 8
    scale_width: int = 1600
    max_step_seconds: float = 45.0
    grid_interval: float = 30.0

class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)

def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load JSON/YAML config and apply documented environment overrides."""
    data: dict[str, Any] = {}
    candidate = Path(path) if path else Path("config.json")
    if candidate.exists():
        if candidate.suffix.lower() == ".json":
            data = json.loads(candidate.read_text(encoding="utf-8"))
        else:
            try:
                import yaml
                data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            except ImportError as exc:
                raise RuntimeError("YAML config requires the dev yaml extra") from exc
    llm = data.setdefault("llm", {})
    for env, field in (("OPENAI_BASE_URL", "base_url"), ("OPENAI_API_KEY", "api_key"), ("OPENAI_MODEL", "enricher_model")):
        if os.getenv(env): llm[field] = os.environ[env]
    if os.getenv("OPENAI_MODEL"):
        for field in ("frame_qa_model", "element_locator_model", "qa_verifier_model"):
            llm[field] = os.environ["OPENAI_MODEL"]
    if os.getenv("PORT"):
        data.setdefault("server", {})["port"] = int(os.environ["PORT"])
    return AppConfig.model_validate(data)

class PipelineConfig(BaseModel):
    """Parameters passed to the media pipeline (legacy compatible API)."""
    video_path: Path
    output_dir: Path = Field(default_factory=lambda: Path("output_manual"))
    mic_track_index: int = 1
    system_track_index: int = 2
    transcription_engine: str = "local-whisper"
    whisper_model: str = "medium"
    whisper_language: str = "pl"
    track: str = "auto"
    diarization: bool = False
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    silence_noise_db: float = -40.0
    min_pause_seconds: float = 1.2
    scene_threshold: float = 0.35
    phash_threshold: int = 8
    scale_width: int = 1600
    document_title: str = "Instrukcja Powdrożeniowa"
