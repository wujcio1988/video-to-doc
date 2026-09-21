"""
Konfiguracja aplikacji Video-to-Doc (VTD).
Wspiera konfigurację z plików JSON/YAML, zmiennych środowiskowych oraz flag CLI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Konfiguracja dostawcy LLM zgodnego z OpenAI API."""
    provider: str = Field(default="openai", description="Nazwa providera: openai, openrouter, ollama, custom, omniroute")
    base_url: str = Field(default="https://api.openai.com/v1", description="Adres bazowy API OpenAI-compatible")
    api_key: Optional[str] = Field(default=None, description="Klucz API (opcjonalny, preferowana zmienna środowiskowa)")
    api_key_env: str = Field(default="OPENAI_API_KEY", description="Nazwa zmiennej środowiskowej z kluczem API")
    timeout: float = Field(default=60.0, description="Timeout zapytań HTTP w sekundach")
    max_retries: int = Field(default=3, description="Liczba ponowień przy błędach sieciowych")

    # Modele dla poszczególnych ról w pipeline
    enricher_model: str = Field(default="gpt-4o-mini", description="Model do redagowania i fuzji instrukcji")
    frame_qa_model: str = Field(default="gpt-4o-mini", description="Model multimodalny do oceny czytelności klatek")
    element_locator_model: str = Field(default="gpt-4o-mini", description="Model do planowania adnotacji UI")
    qa_verifier_model: str = Field(default="gpt-4o-mini", description="Model do weryfikacji jakości dokumentu")

    def get_api_key(self) -> str:
        """Pobiera aktywny klucz API z konfiguracji lub środowiska."""
        if self.api_key and self.api_key.strip():
            return self.api_key.strip()
        
        # Sprawdzenie zmiennej dedykowanej
        val = os.environ.get(self.api_key_env, "")
        if val and val.strip():
            return val.strip()

        # Popularne fallbacki zmiennych
        for env_var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "OMNIROUTE_API_KEY"):
            val = os.environ.get(env_var, "")
            if val and val.strip():
                return val.strip()

        return ""


class StorageConfig(BaseModel):
    """Konfiguracja ścieżek zapisu i plików roboczych."""
    recordings_dir: Path = Field(default_factory=lambda: Path("./recordings"))
    output_dir: Path = Field(default_factory=lambda: Path("./output"))
    data_dir: Path = Field(default_factory=lambda: Path("./data"))
    reference_docs_dir: Optional[Path] = Field(default=None, description="Opcjonalny katalog wiedzy / referencji Markdown")

    def ensure_dirs(self) -> None:
        """Tworzy katalogi jeśli nie istnieją."""
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


class ServerConfig(BaseModel):
    """Konfiguracja serwera webowego Studio."""
    host: str = Field(default="127.0.0.1", description="Adres nasłuchiwania")
    port: int = Field(default=9870, description="Port usługi")


class PipelineConfig(BaseModel):
    """Parametry techniczne potoku wideo -> instrukcja krok po kroku."""
    video_path: Optional[Path] = None
    output_dir: Path = Field(default_factory=lambda: Path("./output"))

    # Audio i ścieżki
    mic_track_index: int = 1
    system_track_index: int = 2
    track_mode: str = "auto"  # "auto", "track1", "track2", "mix"

    # Parametry Whisper ASR
    whisper_model: str = "large-v3"
    whisper_language: str = "pl"
    whisper_device: str = "cpu"  # "cuda" lub "cpu"
    whisper_compute_type: str = "int8"  # "float16" dla GPU, "int8" dla CPU

    # Detekcja ciszy i scen
    silence_noise_db: float = -40.0
    min_pause_seconds: float = 1.2
    scene_threshold: float = 0.35
    phash_threshold: int = 8
    scale_width: int = 1600

    # Metadane dokumentacji
    document_title: str = "Instrukcja Obsługi"
    client: Optional[str] = None
    process: Optional[str] = None
    module: Optional[str] = None
    environment: Optional[str] = None
    author: Optional[str] = None
    document_status: str = "DRAFT"
    mode: str = "manual"  # "manual" lub "meeting"
    output_mode: str = "both"  # "docx", "html", "both"

    # Moduły AI
    frame_qa: bool = False
    annotate: bool = False
    min_step_seconds: float = 6.0
    max_step_seconds: float = 45.0
    grid_interval: float = 30.0


class AppConfig(BaseModel):
    """Główna konfiguracja aplikacji Video-to-Doc."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


def _load_env_file(path: Path) -> None:
    """Prosty loader pliku .env bez wymogu zewnętrznych bibliotek."""
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """
    Ładuje konfigurację z pliku JSON/YAML lub zwraca wartości domyślne.
    Automatycznie wczytuje zmienne z .env jeśli plik istnieje w bieżącym katalogu.
    """
    # Sprawdź .env w cwd lub ~/.vtd/.env
    _load_env_file(Path(".env"))
    _load_env_file(Path.home() / ".vtd" / ".env")

    cfg_dict: Dict[str, Any] = {}

    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    else:
        candidates.extend([
            Path("config.json"),
            Path("config.yaml"),
            Path("config.yml"),
            Path.home() / ".vtd" / "config.json",
            Path.home() / ".vtd" / "config.yaml",
        ])

    for cand in candidates:
        if cand.is_file():
            try:
                if cand.suffix.lower() == ".json":
                    cfg_dict = json.loads(cand.read_text(encoding="utf-8"))
                    break
                elif cand.suffix.lower() in (".yaml", ".yml"):
                    try:
                        import yaml  # type: ignore
                        cfg_dict = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                        break
                    except ImportError:
                        pass
            except Exception:
                pass

    cfg = AppConfig(**cfg_dict)

    # Bezpośrednie nadpisywanie ze zmiennych środowiskowych (kluczowe dla Dockera)
    base_url_env = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or os.environ.get("VTD_BASE_URL")
    if base_url_env and base_url_env.strip():
        cfg.llm.base_url = base_url_env.strip()

    api_key_env = os.environ.get("OPENAI_API_KEY") or os.environ.get("VTD_API_KEY")
    if api_key_env and api_key_env.strip():
        cfg.llm.api_key = api_key_env.strip()

    model_env = os.environ.get("OPENAI_MODEL") or os.environ.get("VTD_MODEL")
    if model_env and model_env.strip():
        m = model_env.strip()
        cfg.llm.enricher_model = m
        cfg.llm.frame_qa_model = m
        cfg.llm.element_locator_model = m
        cfg.llm.qa_verifier_model = m

    port_env = os.environ.get("PORT") or os.environ.get("VTD_PORT")
    if port_env and port_env.strip():
        try:
            cfg.server.port = int(port_env.strip())
        except ValueError:
            pass

    host_env = os.environ.get("HOST") or os.environ.get("VTD_HOST")
    if host_env and host_env.strip():
        cfg.server.host = host_env.strip()

    rec_dir = os.environ.get("RECORDINGS_DIR") or os.environ.get("VTD_RECORDINGS_DIR")
    if rec_dir and rec_dir.strip():
        cfg.storage.recordings_dir = Path(rec_dir.strip())

    out_dir = os.environ.get("OUTPUT_DIR") or os.environ.get("VTD_OUTPUT_DIR")
    if out_dir and out_dir.strip():
        cfg.storage.output_dir = Path(out_dir.strip())

    return cfg
