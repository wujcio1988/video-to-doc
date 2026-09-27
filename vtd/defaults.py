"""GF-016: jedno źródło prawdy dla domyślnych modeli LLM.

Wcześniej trzy różne domyślne modele (CLI "erp-manual", Studio "artur-daily",
settings_store "gpt-4o-mini"), a walidacja w Studio fallbackowała na model
NIEBĘDĄCY na liście ALLOWED_MODELS. Teraz: jeden default, walidacja dopuszcza
wyłącznie modele z ALLOWED_MODELS.
"""
from __future__ import annotations

DEFAULT_ENRICH_MODEL = "gpt-4o-mini"

__all__ = ["DEFAULT_ENRICH_MODEL"]
