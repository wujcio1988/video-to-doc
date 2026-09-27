"""Testy image_enhancer + regresje GF-004 (de-hardcodowanie, usunięcie martwego enhance_frame)."""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from vtd.core import image_enhancer


@pytest.fixture()
def enh(tmp_path, monkeypatch):
    """Przeładowuje moduł z env wskazującym tmp (bez dotykania ~/.hermes)."""
    monkeypatch.setenv("VTD_OMNIROUTE_BASE", "http://example.invalid:9999/v1")
    monkeypatch.setenv("VTD_KEY_FILES", f"{tmp_path}/k.env:{tmp_path}/k2.env")
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    mod = importlib.reload(image_enhancer)
    yield mod, tmp_path
    importlib.reload(image_enhancer)


def test_gf004_omniroute_base_from_env(enh):
    mod, tmp = enh
    assert mod.OMNIROUTE_BASE == "http://example.invalid:9999/v1"
    assert "127.0.0.1:20128" not in mod.OMNIROUTE_BASE


def test_gf004_key_files_from_env(enh):
    mod, tmp = enh
    assert mod._KEY_FILES == [Path(tmp) / "k.env", Path(tmp) / "k2.env"]
    assert not any(str(p).endswith(".hermes/.env") for p in mod._KEY_FILES)
    assert not any("profiles/research" in str(p) for p in mod._KEY_FILES)


def test_gf004_regression_dead_enhance_frame_removed(enh):
    """GF-004: martwa funkcja enhance_frame() (zostawiała _enh_raw.png) usunięta."""
    mod, _ = enh
    assert not hasattr(mod, "enhance_frame"), \
        "enhance_frame() nie jest używana w flow CLI i została usunięta"


def test_resolve_gemini_key_env_priority(enh):
    mod, _ = enh
    assert mod.resolve_gemini_key() is None
    import os
    os.environ["GEMINI_API_KEY"] = "gem"
    try:
        assert mod.resolve_gemini_key() == "gem"
    finally:
        del os.environ["GEMINI_API_KEY"]
    os.environ["OMNIROUTE_API_KEY"] = "omni"
    try:
        assert mod.resolve_gemini_key() == "omni", "OMNIROUTE ma priorytet"
    finally:
        del os.environ["OMNIROUTE_API_KEY"]


def test_verify_target_mocked_http(enh, tmp_path):
    """Mock HTTP: weryfikator celu interpretuje odpowiedź TAK z gatewaya (koszt 0)."""
    mod, _ = enh
    orig = tmp_path / "o.png"
    enh_img = tmp_path / "e.png"
    orig.write_bytes(b"png")
    enh_img.write_bytes(b"png")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices": [{"message": {"content": "TAK - element zgodny z celem"}}]}'

    with patch.object(mod, "_urlopen_with_retry", return_value=_FakeResp()):
        ok, reason, cost = mod.verify_target(
            orig_path=orig, enhanced_path=enh_img, api_key="k",
            step_text="txt", target_description="cel",
        )
    assert ok is True
    assert cost == 0.0


def test_ocr_text_mocked_subprocess(enh, tmp_path, monkeypatch):
    mod, _ = enh
    frame = tmp_path / "f.png"
    frame.write_bytes(b"png")

    class _R:
        stdout = "PRZYCISK ZAPISZ\n"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: _R())
    assert "ZAPISZ" in mod._ocr_text(frame)


def test_enhance_result_fields(enh):
    mod, _ = enh
    r = mod.EnhanceResult(output_path="p", model="m", latency_s=1.0,
                          tokens_in=2, tokens_out=3, cost_usd=0.025)
    assert r.cost_usd == 0.025
