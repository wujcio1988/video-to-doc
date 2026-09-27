"""Testy qa_verifier + regresja GF-002 (usunięcie magicznego hacka '5200')."""
import json
from unittest.mock import patch

from vtd.core import qa_verifier


class _FakeProvider:
    def __init__(self, content=None, error=False):
        self.content = content
        self.error = error

    def chat_completion(self, **kwargs):
        if self.error:
            raise RuntimeError("LLM niedostępny")
        return self.content


def _run_audit(data, provider):
    with patch.object(qa_verifier, "load_config"), \
         patch.object(qa_verifier, "get_llm_provider", return_value=provider):
        return qa_verifier.audit_and_fix_manual(
            enriched_data=data, steps=[], title="T", model="test-model"
        )


def test_gf002_regression_5200_not_mangled():
    """GF-002: legalna liczba 5200 w danych NIE jest już podmieniana regexem."""
    data = {"meta": {}, "steps": [{"step_number": 1, "description": "Zamów 5200 sztuk"}]}
    result, issues = _run_audit(data, _FakeProvider(error=True))
    assert json.dumps(result, ensure_ascii=False) == json.dumps(data, ensure_ascii=False)
    assert not any("KOREKTA LICZBOWA" in i for i in issues)


def test_gf002_regression_5200_as_amount():
    """GF-002: kwota/kod produktu 5200 pozostaje nietknięta."""
    data = {"meta": {"amount": 5200, "code": "5200"}, "steps": []}
    result, _ = _run_audit(data, _FakeProvider(error=True))
    assert result["meta"]["amount"] == 5200
    assert result["meta"]["code"] == "5200"


def test_llm_notes_appended():
    """Uwagi z audytu LLM trafiają do listy issues; dane bez corrected_data nietknięte."""
    data = {"meta": {}, "steps": []}
    content = json.dumps({"qa_status": "DRAFT", "qa_notes": ["uwaga 1"], "corrected_data": None})
    result, issues = _run_audit(data, _FakeProvider(content=content))
    assert "uwaga 1" in issues
    assert result == data


def test_llm_corrected_data_applied():
    """Poprawione dane z LLM są zwracane gdy zawierają steps."""
    data = {"meta": {}, "steps": [{"step_number": 1}]}
    corrected = {"meta": {}, "steps": [{"step_number": 1, "title": "Poprawiony"}]}
    content = json.dumps({"qa_notes": [], "corrected_data": corrected})
    result, _ = _run_audit(data, _FakeProvider(content=content))
    assert result == corrected


def test_llm_error_not_fatal():
    """Błąd LLM nie wysypuje audytu — dane zwracane, błąd opisany w issues."""
    data = {"meta": {}, "steps": []}
    result, issues = _run_audit(data, _FakeProvider(error=True))
    assert result == data
    assert any("Pomocniczy audyt LLM pominięty" in i for i in issues)
