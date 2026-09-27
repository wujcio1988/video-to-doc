"""Regresje Docker/pakowania: DS-004 (.env nie trafia do obrazu)."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_ds004_dockerignore_excludes_env():
    """.env z kluczami NIE może wejść do warstwy obrazu (COPY . . w Dockerfile)."""
    content = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    lines = {ln.strip() for ln in content.splitlines()}
    assert ".env" in lines, ".dockerignore musi wykluczać .env (DS-004)"
    assert "*.env" in lines, ".dockerignore musi wykluczać *.env (DS-004)"
    assert not any(ln.startswith("!") and ".env" in ln for ln in lines), (
        "brak negacji (.env może nie być anulowane w .dockerignore)"
    )
