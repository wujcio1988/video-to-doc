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
    timeout: float = Field(default=180.0, description="Timeout zapytań HTTP w sekundach")
    max_retries: int = Field(default=3, description="Liczba ponowień przy błędach sieciowych")

    # Modele dla poszczególnych ról w pipeline
    enricher_model: str = Field(default="opencode-go/minimax-m3", description="Model do redagowania i fuzji instrukcji")
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

        # GF-009: klucz API sprzężony 1:1 z providerem (base_url) — klucz jednego
        # serwisu nie jest już wysyłany do innego. Gateway lokalny (omniroute/localhost)
        # akceptuje dodatkowo OPENAI_API_KEY jako klucz proxy.
        base = (self.base_url or "").lower()
        if "openrouter" in base:
            candidates = ("OPENROUTER_API_KEY",)
        elif "omniroute" in base or "127.0.0.1" in base or "localhost" in base:
            candidates = ("OMNIROUTE_API_KEY", "OPENAI_API_KEY")
        else:
            candidates = ("OPENAI_API_KEY",)
        for env_var in candidates:
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


# Wartości wstrzyknięte z plików .env — nie są "prawdziwymi" zmiennymi środowiskowymi
# i NIE mogą nadpisywać wartości z jawnego pliku konfiguracji (VTD-029).
_dotenv_injected: Dict[str, str] = {}


def _is_real_env(name: str) -> bool:
    """True jeśli zmienna pochodzi z realnego środowiska procesu, nie z pliku .env."""
    if name not in os.environ:
        return False
    injected = _dotenv_injected.get(name)
    return injected is None or os.environ[name] != injected


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
                _dotenv_injected[key] = val
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
                        # GF-005: ciche pomijanie zastąpione jawnym ostrzeżeniem —
                        # w instalacji produkcyjnej (bez extras dev) plik YAML byłby ignorowany bez komunikatu.
                        print(
                            f"OSTRZEŻENIE: znaleziono plik konfiguracji YAML ({cand}), ale biblioteka "
                            f"'pyyaml' nie jest zainstalowana — plik został POMINIĘTY. "
                            f"Zainstaluj: pip install pyyaml (lub użyj konfiguracji JSON)."
                        )
            except Exception as e:
                print(f"OSTRZEŻENIE: nie można wczytać pliku konfiguracji {cand}: {e}")

    cfg = AppConfig(**cfg_dict)

    # Nadpisywanie ze zmiennych środowiskowych (VTD-029).
    # Precedencja: realna zmienna środowiskowa > plik konfiguracji > plik .env > domyślne.
    # Wartości wstrzyknięte z pliku .env NIE nadpisują pól ustawionych w pliku konfiguracji.
    def _file_set(*path: str) -> bool:
        node: Any = cfg_dict
        for key in path:
            if not isinstance(node, dict) or key not in node or node[key] is None:
                return False
            node = node[key]
        return True

    def _env_pick(*names: str, file_path: tuple) -> Optional[str]:
        for n in names:
            if _is_real_env(n):
                val = os.environ.get(n, "").strip()
                if val:
                    return val
        if not _file_set(*file_path):
            for n in names:
                val = _dotenv_injected.get(n, "").strip()
                if val:
                    return val
        return None

    base_url_env = _env_pick("OPENAI_BASE_URL", "OPENAI_API_BASE", "VTD_BASE_URL", file_path=("llm", "base_url"))
    if base_url_env:
        cfg.llm.base_url = base_url_env

    api_key_env = _env_pick("OPENAI_API_KEY", "VTD_API_KEY", file_path=("llm", "api_key"))
    if api_key_env:
        cfg.llm.api_key = api_key_env

    for model_field in ("enricher_model", "frame_qa_model", "element_locator_model", "qa_verifier_model"):
        model_env = _env_pick("OPENAI_MODEL", "VTD_MODEL", file_path=("llm", model_field))
        if model_env:
            setattr(cfg.llm, model_field, model_env)

    port_env = _env_pick("PORT", "VTD_PORT", file_path=("server", "port"))
    if port_env:
        try:
            cfg.server.port = int(port_env)
        except ValueError:
            pass

    host_env = _env_pick("HOST", "VTD_HOST", file_path=("server", "host"))
    if host_env:
        cfg.server.host = host_env

    rec_dir = _env_pick("RECORDINGS_DIR", "VTD_RECORDINGS_DIR", file_path=("storage", "recordings_dir"))
    if rec_dir:
        cfg.storage.recordings_dir = Path(rec_dir)

    out_dir = _env_pick("OUTPUT_DIR", "VTD_OUTPUT_DIR", file_path=("storage", "output_dir"))
    if out_dir:
        cfg.storage.output_dir = Path(out_dir)

    return cfg
