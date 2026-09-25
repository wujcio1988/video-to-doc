"""Nano Banana (Gemini Flash Image) — AI enhancement klatek dla Video-to-Doc Studio.

Zasady twarde:
- AI dostaje KONTEKST KROKU (narrację) i sam WIDZI UI — więc wskazuje właściwy element
  (rozwiązuje problem deterministycznych adnotacji, które trafiały w sąsiednie przyciski).
- BEZWZGLĘDNY zakaz zmiany treści — walidowany automatycznie OCR-diff (tesseract przed/po).
  Rozbieżność tekstu -> REJECT, do dokumentu wraca oryginał.
- Każdy wynik zapisywany jako NOWY plik (frames_enhanced/), oryginał nigdy nie modyfikowany.
"""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "cheaperinference/nano-banana-2"
# Tryb composite przez OmniRoute (local gateway): JSON na /v1/images/generations
# z image=dataURI; warstwa wraca jako b64_json (zwykle 1024x1024 -> compose robi resize).
OMNIROUTE_BASE = "http://127.0.0.1:20128/v1"
# Weryfikator celu: lancuch modeli — pierwszy hop erp-code (tani), fallback artur-daily
# (gemini-3.8-flash). Combo erp-code po restarcie gatewaya moze trafic na hop tekstowy
# (deepseek/kimi — bez widzenia); wtedy model zwraca BRAK_WIDZENIA i idzie nastepny.
VERIFY_MODELS = ["cheaperinference/gemini-3.7-flash", "openrouter/google/gemini-3.8-flash"]
NO_VISION_TOKEN = "BRAK_WIDZENIA"
# Modele warstwy: nano-banana-2 x2 proby (deterministyczny 2/2 przy zdrowym upstreamie,
# wycieki przejsciowe). grok-imagine WYKLUCZONY (nie rozumie warstwy: 0.1% bieli).
LAYER_MODELS = ["cheaperinference/nano-banana-2", "cheaperinference/nano-banana-2"]
VERIFY_COST_IN = 0.0
VERIFY_COST_OUT = 0.0
# Koszt: Cheaper Inference nano-banana-2 — stawka per obraz (~$0.02-0.03); zero tokenowego
# rozliczania (gateway liczy per image). Weryfikator celu przez OmniRoute = koszt ~0.
FLAT_COST_PER_IMAGE = 0.025
IMAGE_OUT_TOKEN_RATE_31 = 60.0 / 1e6

_KEY_FILES = [
    Path.home() / ".hermes" / ".env",
    Path.home() / ".hermes" / "profiles" / "research" / ".env",
]


class EnhanceError(Exception):
    pass


@dataclass
class EnhanceResult:
    output_path: str
    model: str
    latency_s: float
    tokens_in: int
    tokens_out: int
    cost_usd: float


def resolve_gemini_key() -> Optional[str]:
    """Klucz gatewaya (OmniRoute): env -> ~/.hermes/.env. Szuka OMNIROUTE_API_KEY,
    fallback na GEMINI_API_KEY (legacy AI Studio path). Zwraca None gdy brak."""
    for var in ("OMNIROUTE_API_KEY", "GEMINI_API_KEY"):
        key = os.environ.get(var, "").strip()
        if key:
            return key
    for p in _KEY_FILES:
        try:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    for var in ("OMNIROUTE_API_KEY", "GEMINI_API_KEY"):
                        if line.startswith(var + "="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
        except Exception:
            continue
    return None


def _cost_for(model: str, tokens_out: int) -> float:
    if model.startswith("gemini-3.1"):
        return max(FLAT_COST_PER_IMAGE, tokens_out * IMAGE_OUT_TOKEN_RATE_31)
    return FLAT_COST_PER_IMAGE


def build_prompt(step_text: str, elements_hint: Optional[list] = None, target_description: str = "") -> str:
    """Prompt edycji: JEDNO oznaczenie KLUCZOWEGO elementu kroku, zero zmian treści.

    elements_hint jest domyślnie IGNOROWANY (hinty z OCR bywają mylące — AI widzi ekran
    lepiej niż OCR). Priorytet ma target_description z kontekstu kroku."""
    if target_description:
        target = (
            "Narysuj DOKŁADNIE JEDNO czerwone oznaczenie (strzałkę LUB kółko) wokół elementu: "
            f"{target_description}. Oznacz TYLKO ten element."
        )
    elif elements_hint:
        target = (
            "Narysuj DOKŁADNIE JEDNO czerwone oznaczenie (strzałkę LUB kółko) wokół elementu: "
            + ", ".join(elements_hint) + ". Oznacz TYLKO ten element."
        )
    else:
        target = (
            "Narysuj DOKŁADNIE JEDNO czerwone oznaczenie (strzałkę LUB kółko) wokół KLUCZOWEGO "
            "elementu interfejsu, o którym mowa w kontekście kroku. Oznacz TYLKO ten element."
        )
    return (
        "Jesteś asystentem dokumentacji powdrożeniowej ERP (Comarch Optima). Edytujesz zrzut ekranu.\n"
        f"{target}\n"
        "Uwaga: podobnie nazwane przyciski/zakładki obok celu NIE są celem — wskazuj precyzyjnie.\n"
        f"KONTEKST KROKU (z narracji nagrania): {step_text[:600] or '(brak)'}\n"
        "BEZWZGLĘDNE ZAKAZY: nie zmieniaj żadnego tekstu, liczb, wartości pól, kolorów interfejsu "
        "ani układu okien. Nie dodawaj żadnych napisów ani własnych elementów UI. "
        "Tylko jedno oznaczenie graficzne (strzałka/kółko) kolorem czerwonym. "
        "Zwróć obraz o TYCH SAMYCH proporcjach jak wejściowy."
    )


def _urlopen_with_retry(req, timeout, attempts: int = 3, backoff_s: float = 3.0):
    """urlopen z retry na przejściowe błędy połączenia (restart gatewaya, reset, timeout).
    HTTPError NIE jest retryowany (4xx/5xx = rozstrzygająca odpowiedź API)."""
    import http.client
    import urllib.error
    last = None
    for attempt in range(attempts):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionError, TimeoutError, OSError) as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(backoff_s * (attempt + 1))
    raise EnhanceError(f"gateway niedostępny po {attempts} próbach: {type(last).__name__}: {last}")


def _omniroute_image_call(frame_path, prompt: str, api_key: str, model: str, timeout_s: int = 300):
    """Generacja warstwy przez OmniRoute: POST /v1/images/generations (JSON, image=dataURI).
    Zwraca (out_bytes, latency, cost_usd)."""
    frame_path = Path(frame_path)
    img_uri = "data:image/png;base64," + base64.b64encode(frame_path.read_bytes()).decode()
    payload = {"model": model, "prompt": prompt, "image": img_uri}
    req = urllib.request.Request(
        f"{OMNIROUTE_BASE}/images/generations",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with _urlopen_with_retry(req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        raise EnhanceError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise EnhanceError(f"{type(e).__name__}: {e}") from e
    latency = time.time() - t0
    d0 = (data.get("data") or [{}])[0]
    out_bytes = None
    if d0.get("b64_json"):
        out_bytes = base64.b64decode(d0["b64_json"])
    elif d0.get("url"):
        with _urlopen_with_retry(d0["url"], timeout=60) as r:
            out_bytes = r.read()
    if not out_bytes:
        raise EnhanceError("brak obrazu w odpowiedzi gatewaya")
    return out_bytes, latency, FLAT_COST_PER_IMAGE


def build_layer_prompt(target_description: str, step_text: str = "") -> str:
    """Prompt warstwy oznaczenia: TYLKO czerwone oznaczenie na czystym białym tle.
    Krytyczne: model NIE może odtwarzać treści zrzutu (żeby warstwa była nakładalna)."""
    target = target_description or "KLUCZOWY element interfejsu, o którym mowa w kontekście kroku"
    return (
        "Zadanie: wygeneruj WARSTWĘ ADNOTACJI do nałożenia na zrzut ekranu ERP.\n"
        f"Narysuj DOKŁADNIE JEDNO czerwone oznaczenie (strzałkę LUB kółko) wskazujące element: {target}.\n"
        "Płótno: CAŁE tło wypełnij jednolitą bielą (czysta biała kartka).\n"
        "ABSOLUTNY ZAKAZ: nie rysuj, nie odtwarzaj i nie przerysowuj ŻADNEJ części zrzutu — "
        "żadnego tekstu, okien, pól, przycisków, kolorów aplikacji. Tylko jedno czerwone "
        "oznaczenie na czystej białej kartce. Bez napisów, bez cieni, bez ramek.\n"
        "Oznaczenie skieruj w prawidłową pozycję elementu względem układu zrzutu (załączony obraz "
        "pokazuje układ, który masz naśladować WYŁĄCZNIE dla pozycji oznaczenia — nie dla treści).\n"
        f"Kontekst kroku: {step_text[:400] or '(brak)'}"
    )


def validate_layer_uniformity(layer_path, min_white: float = 0.95, min_red: int = 30) -> Tuple[bool, str]:
    """Warstwa musi być w przewadze bielą (>=95%) + zawierać czerwone oznaczenie.
    Próg 0.95: nano-banana-2 dodaje lekki szum renderingowy (97-98.5% bieli) — to nie jest
    wyciek UI; o jakości pozycji decyduje weryfikator celu (wizyjny TAK/NIE).
    Faktyczny wyciek UI (przerenderowany ekran) ma <80% bieli -> odrzucany z zapasem."""
    from PIL import Image
    try:
        img = Image.open(layer_path).convert("RGB")
    except Exception as e:
        return False, f"warstwa nieotwieralna: {e}"
    small = img.resize((256, max(1, int(256 * img.height / img.width))))
    px = list(small.getdata())
    total = len(px)
    white = sum(1 for r, g, b in px if r > 240 and g > 240 and b > 240)
    red = sum(1 for r, g, b in px if r > 150 and r > g + 50 and r > b + 50)
    other = total - white - red
    if white / total < min_white:
        return False, f"warstwa niejednolita: tylko {100*white/total:.1f}% bieli (wyciek treści UI: {100*other/total:.1f}%)"
    if red < min_red:
        return False, f"brak czerwonego oznaczenia na warstwie ({red} px próbkowanych)"
    return True, f"warstwa OK: {100*white/total:.1f}% bieli, {red} px czerwieni"


def compose(orig_path, layer_path, out_path) -> Tuple[bool, str]:
    """Alpha-composite: warstwa (biala + czerwone oznaczenie) nakładana na ORYGINAŁ.
    Oryginał pozostaje piksel-perfekt poza obszarem oznaczenia."""
    from PIL import Image
    orig = Image.open(orig_path).convert("RGBA")
    layer = Image.open(layer_path).convert("RGBA")
    if layer.size != orig.size:
        layer = layer.resize(orig.size)
    # alpha = nie-białość: białe tło -> przezroczyste, czerwone oznaczenie -> nieprzezroczyste
    r, g, b, _ = layer.split()
    from PIL import ImageOps, ImageChops
    min_channel = ImageChops.darker(ImageChops.darker(r, g), b)
    alpha = ImageOps.invert(min_channel)  # 255-white -> 0 alpha; red(min~28) -> ~227 alpha
    layer.putalpha(alpha)
    composed = Image.alpha_composite(orig, layer)
    composed.convert("RGB").save(out_path, "PNG")
    return True, str(out_path)


def _gemini_text_call(parts: list, api_key: str, model: str, timeout_s: int = 60) -> Tuple[dict, float, int, int]:
    """Pomocnicze: wywołanie generateContent, zwraca (response, latency, tokens_in, tokens_out)."""
    url = f"{API_BASE}/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": parts}]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        with _urlopen_with_retry(req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        raise EnhanceError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise EnhanceError(f"{type(e).__name__}: {e}") from e
    latency = time.time() - t0
    usage = data.get("usageMetadata", {})
    return data, latency, int(usage.get("promptTokenCount", 0) or 0), int(usage.get("candidatesTokenCount", 0) or 0)


def verify_target(
    orig_path,
    enhanced_path,
    api_key: str,
    step_text: str = "",
    target_description: str = "",
) -> Tuple[bool, str, float]:
    """Wizyjna weryfikacja celu przez OmniRoute (2 obrazy + pytanie TAK/NIE).
    Lancuch VERIFY_MODELS: model bez widzenia odpowiada BRAK_WIDZENIA -> proba nastepnego.
    Zwraca (ok, uzasadnienie, koszt_usd)."""
    o_uri = "data:image/png;base64," + base64.b64encode(Path(orig_path).read_bytes()).decode()
    e_uri = "data:image/png;base64," + base64.b64encode(Path(enhanced_path).read_bytes()).decode()
    question = (
        "Obraz 1 to oryginalny zrzut ERP. Obraz 2 to ta sama aplikacja z CZERWONYM oznaczeniem "
        "(strzałka/kółko). Kontekst kroku instrukcji: "
        f"\"{(target_description or step_text)[:400]}\".\n"
        "Pytanie: czy czerwone oznaczenie wskazuje element interfejsu, który jest przedmiotem "
        "tego kroku? Odpowiedz WYŁĄCZNIE jednym słowem TAK lub NIE, potem w nowej linii jedno "
        "zdanie uzasadnienia. Jeśli nie widzisz załączonych obrazów, odpowiedz dokładnie: "
        f"{NO_VISION_TOKEN}."
    )
    last_reason = "brak odpowiedzi modelu"
    for model in VERIFY_MODELS:
        payload = {
            "model": model,
            # jawny maly limit: TAK/NIE + 1 zdanie ~60 tokenow; brak limitu = gateway
            # default 65536 -> HTTP 402 przy niskim saldzie (openrouter: "can only afford N")
            "max_tokens": 300,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": o_uri}},
                {"type": "image_url", "image_url": {"url": e_uri}},
                {"type": "text", "text": question},
            ]}],
        }
        req = urllib.request.Request(
            f"{OMNIROUTE_BASE}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        try:
            with _urlopen_with_retry(req, timeout=120) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            last_reason = f"HTTP {e.code}: {detail}"
            continue
        except Exception as e:
            last_reason = f"{type(e).__name__}: {e}"
            continue
        msg = data.get("choices", [{}])[0].get("message", {})
        text = msg.get("content", "") or ""
        if not isinstance(text, str):
            text = json.dumps(text)
        stripped = text.strip()
        if NO_VISION_TOKEN in stripped.upper() or (
            not stripped.upper().startswith("TAK") and not stripped.upper().startswith("NIE")
        ):
            last_reason = f"{model}: brak widzenia/odpowiedzi ({stripped[:80]!r})"
            continue
        verdict_ok = stripped.upper().startswith("TAK")
        reason = stripped.splitlines()[-1][:200] if stripped else last_reason
        return verdict_ok, reason, 0.0
    raise EnhanceError(f"weryfikator celu niedostępny: {last_reason}")


def enhance_frame(
    frame_path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    step_text: str = "",
    elements_hint: Optional[list] = None,
    target_description: str = "",
    timeout_s: int = 120,
) -> EnhanceResult:
    """Wyślij klatkę do nano banana, zapisz wynik jako <stem>_enh_raw.png obok oryginału (tmp)."""
    frame_path = Path(frame_path)
    img_b64 = base64.b64encode(frame_path.read_bytes()).decode()
    url = f"{API_BASE}/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": img_b64}},
                    {"text": build_prompt(step_text, elements_hint, target_description)},
                ]
            }
        ]
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    try:
        with _urlopen_with_retry(req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        raise EnhanceError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise EnhanceError(f"{type(e).__name__}: {e}") from e
    latency = time.time() - t0

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    out_bytes = None
    for p in parts:
        if "inlineData" in p and p["inlineData"].get("data"):
            out_bytes = base64.b64decode(p["inlineData"]["data"])
            break
    if not out_bytes:
        raise EnhanceError("brak obrazu w odpowiedzi modelu")

    usage = data.get("usageMetadata", {})
    tokens_in = int(usage.get("promptTokenCount", 0) or 0)
    tokens_out = int(usage.get("candidatesTokenCount", 0) or 0)

    out_path = frame_path.parent / (frame_path.stem + "_enh_raw.png")
    out_path.write_bytes(out_bytes)
    return EnhanceResult(
        output_path=str(out_path),
        model=model,
        latency_s=latency,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=_cost_for(model, tokens_out),
    )


def _ocr_text(path) -> str:
    try:
        r = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", "pol+eng", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout or ""
    except Exception:
        return ""


def _norm(text: str) -> str:
    import re
    return re.sub(r"[^a-ząćęłńóśźż0-9]+", " ", text.lower()).strip()


def verify_text_fidelity(orig_path, enhanced_path, min_len: int = 4) -> Tuple[bool, str]:
    """OCR-diff: każde słowo (>=min_len znaków) z ORYGINAŁU musi być w wersji AI.
    Plus kontrola proporcji (dryf >12% -> reject). Zwraca (ok, raport)."""
    from PIL import Image

    try:
        wo, ho = Image.open(orig_path).size
        we, he = Image.open(enhanced_path).size
    except Exception as e:
        return False, f"nie można otworzyć obrazów: {e}"
    if wo and ho:
        ratio_o, ratio_e = wo / ho, we / he
        if abs(ratio_e - ratio_o) / max(ratio_o, 1e-6) > 0.12:
            return False, f"dryf proporcji {ratio_o:.2f} -> {ratio_e:.2f}"

    orig_norm = _norm(_ocr_text(orig_path))
    if not orig_norm:
        return True, "oryginał bez czytelnego tekstu (OCR puste) — brak czego chronić"
    enh_norm = _norm(_ocr_text(enhanced_path))
    missing = [w for w in set(orig_norm.split()) if len(w) >= min_len and w not in enh_norm]
    if missing:
        return False, "brakujące/nieczytelne słowa po AI: " + ", ".join(sorted(missing)[:8])
    return True, "tekst zachowany (OCR-diff OK)"


def enhance_frame_composite(
    frame_path,
    api_key: str,
    target_description: str = "",
    step_text: str = "",
    model: str = DEFAULT_MODEL,
    output_dir=None,
    verify_goal: bool = True,
) -> dict:
    """Tryb COMPOSITE (Opcja A): AI generuje TYLKO warstwę oznaczenia (biała kartka + czerwone
    oznaczenie), kod nakłada ją na oryginał (piksel-perfekt). Walidacje: jednolitość warstwy,
    weryfikator celu (wizyjny TAK/NIE) na kompozycie.

    Zwraca {"status": "accepted"|"rejected", "path": str|None, "reason": str, "cost_usd": float,
            "model": str, "target_ok": bool|None}.
    """
    import io
    frame_path = Path(frame_path)
    out = {"status": "rejected", "path": None, "reason": "", "cost_usd": 0.0, "model": model, "target_ok": None}

    # 1) generacja warstwy — łańcuch modeli (wyciek bywa niedeterministyczny per call)
    layer_models = [model] + [m for m in LAYER_MODELS if m != model]
    last_reason = ""
    tmp = frame_path.parent / (frame_path.stem + "_layer_raw.png")
    for lm in layer_models:
        try:
            out_bytes, _lat, cost = _omniroute_image_call(
                frame_path, build_layer_prompt(target_description, step_text), api_key, lm
            )
        except EnhanceError as e:
            last_reason = f"{lm}: błąd generacji warstwy: {e}"
            continue
        out["cost_usd"] += cost
        tmp.write_bytes(out_bytes)
        ok, report = validate_layer_uniformity(tmp)
        if ok:
            out["model"] = lm
            break
        last_reason = f"{lm}: {report}"
    else:
        tmp.unlink(missing_ok=True)
        out["reason"] = last_reason or "generacja warstwy nieudana (wszystkie modele)"
        return out

    # 3) composite na oryginale (wymuszone wymiary oryginału)
    enhanced_dir = Path(output_dir) if output_dir else frame_path.parent
    enhanced_dir.mkdir(parents=True, exist_ok=True)
    out_path = enhanced_dir / frame_path.name
    compose(frame_path, tmp, out_path)
    tmp.unlink(missing_ok=True)

    # 4) weryfikator celu na kompozycie (jeśli włączony)
    if verify_goal:
        try:
            target_ok, target_reason, t_cost = verify_target(
                frame_path, out_path, api_key,
                step_text=step_text, target_description=target_description,
            )
            out["cost_usd"] += t_cost
            out["target_ok"] = target_ok
            if not target_ok:
                out_path.unlink(missing_ok=True)
                out["reason"] = f"CEL NIEOK: {target_reason}"
                return out
        except EnhanceError as e:
            out_path.unlink(missing_ok=True)
            out["reason"] = f"błąd weryfikatora celu: {e}"
            return out

    out["status"] = "accepted"
    out["path"] = str(out_path)
    out["reason"] = "kompozyt zaakceptowany"
    return out


# ---------- LOCATOR (Etap 5): AI wskazuje wspolrzedne, kod rysuje ----------

LOCATOR_MODEL = "cheaperinference/gemini-3.7-flash"
LOCATOR_COST = 0.002  # ~1k tokenow wizyjnych gemini-flash; weryfikacja dalej ~0

# Wersja cache nano-banana: PODBIJ przy każdej zmianie promptów lub łańcuchów modeli,
# żeby stare wyniki nie wracały z dysk-cache (stage_cache.nano_lookup/nano_store).
NANO_CACHE_VERSION = "v1-2026-09-22"


def nano_cache_signature() -> str:
    """Sygnatura niezmienników etapu nano — część klucza cache."""
    return "|".join([
        NANO_CACHE_VERSION,
        LOCATOR_MODEL,
        ",".join(LAYER_MODELS),
        ",".join(VERIFY_MODELS),
        DEFAULT_MODEL,
    ])


def _clamp01(v: float, eps: float = 0.05) -> float:
    """Tolerancyjny clamp: lekki overshoot (±eps) przycinany, wiekszy -> ValueError."""
    v = float(v)
    if -eps <= v <= 1 + eps:
        return min(1.0, max(0.0, v))
    raise ValueError(f"wartosc poza zakresem: {v}")


def locate_target(
    orig_path,
    api_key: str,
    target_description: str = "",
    step_text: str = "",
    model: str = LOCATOR_MODEL,
) -> Tuple[float, float, str, Optional[Tuple[float, float, float, float]], float]:
    """LOCATOR: model wizyjny zwraca WYŁĄCZNIE JSON {"cx": 0..1, "cy": 0..1, "label": str}
    — względne współrzędne ŚRODKA elementu na zrzucie. Model tylko WSKAZUJE (nigdy nie rysuje).
    Zwraca (cx, cy, label, koszt). Raises EnhanceError na śmieciach/poza zakresem."""
    o_uri = "data:image/png;base64," + base64.b64encode(Path(orig_path).read_bytes()).decode()
    target = target_description or "KLUCZOWY element interfejsu, o którym mowa w kontekście kroku"
    prompt = (
        "Wskaż na zrzucie ekranu element: " + target + ".\n"
        "Odpowiedz WYŁĄCZNIE jednym obiektem JSON (bez markdown, bez komentarzy):\n"
        '{"cx": <0.0-1.0>, "cy": <0.0-1.0>, "label": "<nazwa elementu>", '
        '"bbox": {"x0": <0.0-1.0>, "y0": <0.0-1.0>, "x1": <0.0-1.0>, "y1": <0.0-1.0>}}\n'
        "cx/cy = względne współrzędne ŚRODKA elementu (lewy-górny róg zrzutu = 0,0; "
        "prawy-dolny = 1,1). bbox = opcjonalny OTACZAJĄCY prostokąt elementu "
        "(x0,y0 = lewy-górny, x1,y1 = prawy-dolny, względne 0..1). "
        "Nic więcej nie pisz."
    )
    payload = {
        "model": model,
        # locator: model mysli (reasoning tokeny licza sie do limitu) -> 300 ucilo JSON (v13);
        # 1200 przestalo wystarczac po dodaniu bbox do zadania (v16: ucięty mid-bbox). 2200 = zapas.
        "max_tokens": 2200,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": o_uri}},
            {"type": "text", "text": prompt},
        ]}],
    }
    req = urllib.request.Request(
        f"{OMNIROUTE_BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with _urlopen_with_retry(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        raise EnhanceError(f"HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise EnhanceError(f"{type(e).__name__}: {e}") from e
    msg = data.get("choices", [{}])[0].get("message", {})
    text = msg.get("content", "") or ""
    if not isinstance(text, str):
        text = json.dumps(text)
    # parsowanie: odrzuć ewentualne ogonki markdown
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise EnhanceError(f"locator: brak JSON w odpowiedzi: {text[:120]!r}")
    try:
        obj = json.loads(t[start:end + 1])
        cx = _clamp01(obj["cx"])
        cy = _clamp01(obj["cy"])
        label = str(obj.get("label", "")).strip()[:120]
        bbox = _parse_bbox(obj)
    except (KeyError, ValueError, TypeError) as e:
        raise EnhanceError(f"locator: niepoprawne współrzędne: {e} (odp: {text[:120]!r})") from e
    return cx, cy, label, bbox, LOCATOR_COST


def _parse_bbox(obj) -> Optional[Tuple[float, float, float, float]]:
    """Waliduje opcjonalny bbox z locatora: (x0,y0,x1,y1) rel. 0..1.
    Zwraca None gdy brak/śmieci/odwrócone/poza zakresem/anty-szaleństwo (max bok 0.6)."""
    try:
        b = obj.get("bbox")
        if not isinstance(b, dict):
            return None
        x0, y0, x1, y1 = (float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    if (x1 - x0) > 0.6 or (y1 - y0) > 0.6:
        return None
    return (x0, y0, x1, y1)


def _draw_step_badge(img, badge_number: Optional[int]) -> None:
    """Badge numeru kroku: czerwona zaokrąglona plakietka z białym numerem,
    lewy-górny róg (20,20), rozmiar skalowany od wysokości zrzutu. In-place."""
    from PIL import ImageDraw, ImageFont
    if not badge_number:
        return
    W, H = img.size
    s = max(28, int(H * 0.055))
    pad = max(4, s // 6)
    d = ImageDraw.Draw(img)
    x0, y0 = 20, 20
    x1, y1 = x0 + s + 2 * pad, y0 + s + 2 * pad
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", s)
    except OSError:
        font = ImageFont.load_default()
    text = str(int(badge_number))
    tb = d.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.rounded_rectangle(
        (x0, y0, x1, y1), radius=max(6, s // 5), fill=(225, 25, 25),
        outline=(255, 255, 255), width=2,
    )
    d.text(
        (x0 + (x1 - x0 - tw) // 2 - tb[0], y0 + (y1 - y0 - th) // 2 - tb[1]),
        text, font=font, fill=(255, 255, 255),
    )


def draw_annotation(
    orig_path,
    cx: float,
    cy: float,
    out_path,
    color=(225, 25, 25),
    width: int = 6,
    radius_frac: float = 0.13,
    badge_number: Optional[int] = None,
) -> str:
    """Deterministyczne rysowanie: czerwona elipsa wokół (cx,cy) na ORYGINALE
    (+ opcjonalny badge numeru kroku). Piksel-perfekt poza oznaczeniem.
    Zwraca ścieżkę."""
    from PIL import Image, ImageDraw
    img = Image.open(orig_path).convert("RGB")
    W, H = img.size
    px, py = cx * W, cy * H
    r = min(W, H) * radius_frac
    bbox = (px - r * 1.15, py - r * 0.85, px + r * 1.15, py + r * 0.85)
    d = ImageDraw.Draw(img)
    d.ellipse(bbox, outline=color, width=width)
    _draw_step_badge(img, badge_number)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return str(out_path)


def draw_annotation_rect(
    orig_path,
    bbox: Tuple[float, float, float, float],
    out_path,
    color=(225, 25, 25),
    width: int = 5,
    badge_number: Optional[int] = None,
    pad_frac: float = 0.008,
) -> str:
    """Deterministyczne rysowanie: czerwony prostokąt dokoła bboxu elementu
    (rel. 0..1, lekki padding 0.8% + clamp do obrazu), opcjonalny badge numeru
    kroku. Piksel-perfekt poza oznaczeniem. Zwraca ścieżkę."""
    from PIL import Image, ImageDraw
    img = Image.open(orig_path).convert("RGB")
    W, H = img.size
    pw, ph = W * pad_frac, H * pad_frac
    x0 = max(0.0, bbox[0] * W - pw)
    y0 = max(0.0, bbox[1] * H - ph)
    x1 = min(float(W), bbox[2] * W + pw)
    y1 = min(float(H), bbox[3] * H + ph)
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x1, y1), outline=color, width=width)
    _draw_step_badge(img, badge_number)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return str(out_path)


def enhance_frame_located(
    frame_path,
    api_key: str,
    target_description: str = "",
    step_text: str = "",
    output_dir=None,
    verify_goal: bool = True,
    step_number: Optional[int] = None,
) -> dict:
    """Tryb LOCATED (Etap 5): locator (wizja -> JSON cx/cy + opcjonalny bbox) +
    rysowanie przez kod (prostokąt dopasowany do elementu; fallback kółko gdy
    brak bboxu; badge numeru kroku) + weryfikator celu. FALLBACK: błąd locatora
    -> enhance_frame_composite (dotychczasowa ścieżka nano-banana). Zwraca ten
    sam kontrakt co composite (+ 'via')."""
    frame_path = Path(frame_path)
    out = {"status": "rejected", "path": None, "reason": "", "cost_usd": 0.0,
           "model": f"locator:{LOCATOR_MODEL}", "target_ok": None, "via": "located"}
    try:
        cx, cy, label, bbox, l_cost = locate_target(
            frame_path, api_key, target_description, step_text
        )
        out["cost_usd"] += l_cost
    except EnhanceError as e:
        fb = enhance_frame_composite(
            frame_path, api_key,
            target_description=target_description, step_text=step_text,
            output_dir=output_dir, verify_goal=verify_goal,
        )
        fb["via"] = f"composite-fallback ({e})"
        return fb

    enhanced_dir = Path(output_dir) if output_dir else frame_path.parent
    out_path = enhanced_dir / frame_path.name
    if bbox is not None:
        draw_annotation_rect(frame_path, bbox, out_path, badge_number=step_number)
    else:
        draw_annotation(frame_path, cx, cy, out_path, badge_number=step_number)

    if verify_goal:
        try:
            target_ok, target_reason, t_cost = verify_target(
                frame_path, out_path, api_key,
                step_text=step_text, target_description=target_description,
            )
            out["cost_usd"] += t_cost
            out["target_ok"] = target_ok
            if not target_ok and bbox is not None:
                # Tani fallback kształtu: ciasny prostokąt bywa twardszym sędzia
                # (mm-błąd centrowania obnaża pusty obszar); kółko z zapasem
                # przechodzi częściej. Przerysowanie na kółko z tego samego
                # centrum + druga weryfikacja (1 dodatkowe wywołanie, bez
                # nowego locatora). Dopiero potem ścieżka odrzucenia.
                draw_annotation(frame_path, cx, cy, out_path, badge_number=step_number)
                target_ok2, target_reason2, t_cost2 = verify_target(
                    frame_path, out_path, api_key,
                    step_text=step_text, target_description=target_description,
                )
                out["cost_usd"] += t_cost2
                out["target_ok"] = target_ok2
                target_reason = target_reason2
                target_ok = target_ok2
            if not target_ok:
                out_path.unlink(missing_ok=True)
                out["reason"] = f"CEL NIEOK: {target_reason}"
                return out
        except EnhanceError as e:
            out_path.unlink(missing_ok=True)
            out["reason"] = f"błąd weryfikatora celu: {e}"
            return out

    out["status"] = "accepted"
    out["path"] = str(out_path)
    out["reason"] = f"locator ok ({label or 'element'} @ {cx:.2f},{cy:.2f})"
    return out
