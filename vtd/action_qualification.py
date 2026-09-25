"""Conservative, dependency-free qualification of transcript action candidates."""
import re
from typing import Any, Dict, Optional

CONFIRMED_ACTION = "confirmed_action"
EXPLICIT_CONFIGURATION_INSTRUCTION = "explicit_configuration_instruction"
NOT_A_PROCEDURE_STEP = "not_a_procedure_step"

# Keep this vocabulary deliberately narrow: a transcript verb alone is not an
# operation.  The following gates are deterministic and run before any LLM output.
# Only imperative, operator-facing verbs qualify.  First-person plural
# narration ("zatwierdzamy", "wybieramy") is discussion, not an instruction.
_ACTION = re.compile(r"\b(kliknij|naciśnij|wybierz|otwórz|otwieramy|zamknij|wpisz|wprowadź|zaznacz|odznacz|przejdź|uruchom|zapisz|usuń|dodaj|ustaw|zmień|wyślij|zatwierdź)\b", re.I)
_CONFIG = re.compile(r"\b(skonfiguruj|ustaw|zmień|wprowadź|wpisz)\b", re.I)
_NEGATED = re.compile(r"\b(nie trzeba|nie musimy|nie rób|nie klikaj|można|możemy|być może|pytanie|czy kliknąć|omówmy|dyskusj|nie wiem|chyba|pewnie|powinieneś|powinnaś|proponuj|zróbmy|zrobimy|będziemy)\b", re.I)
_COMMENTARY = re.compile(r"\b(teraz widzimy|w tym miejscu (?:widać|opowiem)|jak widać|chciałbym powiedzieć|zobaczmy|przejdźmy do|następnie omówię|tylko te|dobra|aha|no tak|czyli|mamy też opcję|żeby nie)\b", re.I)
_UI_TARGET = re.compile(r"\b(przycisk|pole|okn(?:o|ie)|menu|zakładk|formularz|lista|dokument|zlecenie|parametr|ustawieni|checkbox|polecenie|wartość|kod|numer)\b", re.I)
_DATA = re.compile(r"\b(liczba|ilość|wynosi|widzimy listę|lista dokumentów|dane pokazują|jest [0-9]+)\b", re.I)
_DIALOGUE = re.compile(r"(?:\?|\.{3}|\b(tak|nie|mhm|dobra|okej|no)\b.*\b(tak|nie|mhm)\b)", re.I)


def _looks_like_operational_sentence(text: str) -> bool:
    """Reject meeting prose even when it happens to contain an action verb."""
    words = re.findall(r"\b\w+\b", text, re.UNICODE)
    if len(words) < 2 or len(words) > 32 or len(text) > 240:
        return False
    if _DIALOGUE.search(text) and len(words) > 12:
        return False
    return True


def _has_concrete_target(text: str, action: re.Match) -> bool:
    tail = text[action.end():].strip(" .,:;—-()")
    # A target must follow the verb; generic "zmień parametr" is not enough
    # unless a named field/value is present.
    if not tail:
        return False
    return bool(re.search(r"[A-Za-zÀ-ÿ0-9]{2,}", tail))


def _ui_text(visible_ui: Any) -> str:
    if visible_ui is None:
        return ""
    if isinstance(visible_ui, str):
        return visible_ui
    if isinstance(visible_ui, dict):
        values = [visible_ui.get(k, "") for k in ("ocr_text", "text", "title", "elements")]
        return " ".join(str(v) for v in values)
    return str(visible_ui)


def classify_candidate(text: str, transcript_context: str, visible_ui: Optional[Any] = None) -> Dict[str, Any]:
    """Classify a candidate; ambiguous or descriptive language is rejected."""
    text = str(text or "").strip()
    context = str(transcript_context or "")
    ui = _ui_text(visible_ui)
    reasons = []
    if not text:
        return {"classification": NOT_A_PROCEDURE_STEP, "confidence": 0.0, "reasons": ["empty candidate"], "partial": False}
    if _NEGATED.search(text) or _COMMENTARY.search(text) or not _looks_like_operational_sentence(text):
        return {"classification": NOT_A_PROCEDURE_STEP, "confidence": 0.0, "reasons": ["commentary, discussion, or non-atomic text"], "partial": False}
    action = _ACTION.search(text)
    config = _CONFIG.search(text)
    if not action and not config:
        return {"classification": NOT_A_PROCEDURE_STEP, "confidence": 0.0, "reasons": ["no explicit imperative action"], "partial": False}
    if action and (not _has_concrete_target(text, action) or not _UI_TARGET.search(text)):
        # A named ERP object/window/field/button is a valid target even when
        # the generic UI vocabulary does not match the inflected Polish form.
        target_tail = text[action.end():]
        named_erp_target = bool(re.search(r"\b(zlecen(?:ie|ia|iu)|technologi(?:a|ę|i)|dokument(?:u|em)?|produkcj(?:a|i)|optim(?:a|ę)|CTI)\b", target_tail, re.I))
        if not named_erp_target:
            return {"classification": NOT_A_PROCEDURE_STEP, "confidence": 0.0, "reasons": ["missing concrete UI target"], "partial": False}
    if not action:
        action = config
    if config and not re.search(r"\b(kliknij|wybierz|otwórz|zaznacz|naciśnij)\b", text, re.I):
        classification = EXPLICIT_CONFIGURATION_INSTRUCTION
    else:
        classification = CONFIRMED_ACTION
    target = action.group(0) if action else ""
    if ui and (target.lower() in ui.lower() or any(w.lower() in ui.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]+", text))):
        confidence = 0.95
        reasons.append("action target is present in visible UI/OCR")
    elif visible_ui is None:
        confidence = 0.65 if classification == EXPLICIT_CONFIGURATION_INSTRUCTION else 0.55
        reasons.append("no visible UI evidence")
    else:
        confidence = 0.55
        reasons.append("visible UI does not confirm action target")
    if context:
        reasons.append("transcript context supplied")
    return {"classification": classification, "confidence": confidence, "reasons": reasons, "partial": confidence < 0.8}
