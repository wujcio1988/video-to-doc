"""
Konfiguracja testów pytest dla Video-to-Doc.
"""
import os
import sys
from pathlib import Path

# Dodaj katalog główny projektu do PYTHONPATH
ROOT_DIR = Path(__file__).parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ustaw zmienne środowiskowe dla testów hermetycznych
os.environ["OPENAI_API_KEY"] = "test-mock-key-123"
