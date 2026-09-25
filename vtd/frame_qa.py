import json
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Any
from vtd.fusion import ManualStep

MAX_REVIEWS = 12

def _get_token():
    # Współdziel helper z enricher — nie duplikuj kodu klucza
    try:
        from vtd.enricher import get_omniroute_token as _get
        return _get()
    except Exception:
        return ""

class FrameReviewer:
    def __init__(self, model: str = "erp-code", timeout: int = 120, max_retries: int = 2, token: Optional[str] = None):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.token = token if token is not None else _get_token()

    def review_step_frame(self, step_number: int, step_title: str, step_description: str, frame_path: Path) -> Dict[str, str]:
        try:
            from vtd.enricher import extract_frame_ocr, robust_json_decode
        except Exception:
            return {"verdict": "unknown", "reason": "Brak enricher/OCR"}

        ocr_text = ""
        try:
            ocr_text = extract_frame_ocr(frame_path)
        except Exception:
            ocr_text = ""

        # Prompt PL
        prompt = (
            f"Oceniasz czy zrzut ekranu pokazuje element/okno opisane w kroku instrukcji. "
            f"OCR zrzutu: {ocr_text[:2000] if ocr_text else 'BRAK OCR'}\n"
            f"Opis kroku: {step_title} — {step_description}\n"
            f"Odpowiedz TYLKO JSON {{\"verdict\":\"ok|unclear|wrong\",\"reason\":\"...\"}}. "
            f"ok = zrzut wspiera krok; unclear = zrzut nieczytelny/low-res/nie widać elementu; wrong = zrzut pokazuje inny etap."
        )

        url = "http://127.0.0.1:20128/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Jesteś ekspertem weryfikującym czytelność zrzutów ekranu. Odpowiadaj TYLKO JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        }
        # attempt with retries
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.token}",
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"].strip()
                    # Parse robust
                    try:
                        from vtd.enricher import robust_json_decode as _rjd
                        parsed = _rjd(content)
                    except Exception:
                        # fallback manual
                        parsed = json.loads(content)
                    verdict = str(parsed.get("verdict", "unknown")).strip().lower()
                    reason = str(parsed.get("reason", "")).strip()
                    if verdict not in ("ok", "unclear", "wrong"):
                        # normalize unknown cases but keep if ok/unclear/wrong
                        if verdict not in ("ok", "unclear", "wrong", "unknown"):
                            verdict = "unknown"
                    return {"verdict": verdict, "reason": reason or "Brak powodu"}
            except Exception as e:
                if attempt >= self.max_retries:
                    return {"verdict": "unknown", "reason": f"Błąd sieci/JSON: {e}"}
                continue
        return {"verdict": "unknown", "reason": "Nieznany błąd"}

def improve_step_frame(video_path: Path, step: ManualStep, out_dir: Path, reviewer: Optional[FrameReviewer] = None) -> Optional[Path]:
    """
    Wycina kandydatów z okna kroku, RANKUJE ich po ostrości (Laplacian variance —
    deterministycznie, bez LLM), a następnie review LLM idzie od najostrośniejszego
    (return przy pierwszym ok; max_retries dalej ogranicza liczbę wywołań).
    Fallback: brak numpy -> stare wycięcia 70%/40%.
    Zwraca najlepszą (pierwszą ok, inaczej ostatnią) albo None.
    """
    if reviewer is None:
        reviewer = FrameReviewer()

    duration = step.end_time - step.start_time
    if duration <= 0:
        return None

    candidates = []
    try:
        from vtd.frame_sharpness import extract_sharp_candidates, rank_sharp_frames

        cand_paths = extract_sharp_candidates(
            video_path, step.start_time, step.end_time, out_dir,
            step_number=step.step_number, k=4,
        )
        ranked = rank_sharp_frames(cand_paths)
        candidates = [p for _score, p in ranked]
    except ImportError:
        # Fallback bez numpy: historyczne wycięcia 70% / 40%
        for pct in [0.7, 0.4]:
            t = step.start_time + duration * pct
            fname = f"grid_retry_{step.step_number:04d}_{int(pct*100):02d}.png"
            out_path = out_dir / fname
            counter = 1
            while out_path.exists():
                out_path = out_dir / f"grid_retry_{step.step_number:04d}_{int(pct*100):02d}_{counter}.png"
                counter += 1
            from vtd.frame_extractor import extract_frame_at
            res = extract_frame_at(video_path, t, out_path)
            if res is None or not res.exists():
                continue
            candidates.append(res)

    for res in candidates:
        review = reviewer.review_step_frame(step.step_number, f"Krok {step.step_number}", step.speech_text or "", res)
        verdict = review.get("verdict", "unknown")
        if verdict == "ok":
            return res
        if candidates.index(res) + 1 >= reviewer.max_retries:
            break

    if candidates:
        return candidates[-1]
    return None
