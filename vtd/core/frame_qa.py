"""
Moduł kontroli czytelności klatek przez LLM (Frame QA).
Weryfikuje, czy klatka wycięta ze sceny odpowiada merytorycznie krokowi procedury.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from vtd.config import load_config
from vtd.core.fusion import ManualStep
from vtd.providers import get_llm_provider, robust_json_decode

MAX_REVIEWS = 12


class FrameReviewer:
    """Recenzent jakości i trafności klatek zrzutów ekranu."""

    def __init__(
        self,
        model: Optional[str] = None,
        timeout: int = 120,
        max_retries: int = 2,
    ) -> None:
        cfg = load_config()
        self.model = model or cfg.llm.frame_qa_model
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider = get_llm_provider(cfg.llm)

    def review_step_frame(
        self,
        step_number: int,
        step_title: str,
        step_description: str,
        frame_path: Path,
    ) -> Dict[str, str]:
        """Ocenia czytelność klatki i zgodność z opisem kroku."""
        from vtd.core.enricher import extract_frame_ocr

        ocr_text = ""
        try:
            ocr_text = extract_frame_ocr(frame_path)
        except Exception:
            ocr_text = ""

        prompt = (
            f"Oceniasz czy zrzut ekranu pokazuje element/okno opisane w kroku instrukcji.\n"
            f"OCR zrzutu: {ocr_text[:2000] if ocr_text else 'BRAK OCR'}\n"
            f"Opis kroku: {step_title} — {step_description}\n"
            f"Odpowiedz TYLKO JSON {{\"verdict\":\"ok|unclear|wrong\",\"reason\":\"...\"}}.\n"
            f"ok = zrzut wspiera krok; unclear = zrzut nieczytelny/low-res/nie widać elementu; wrong = zrzut pokazuje inny etap."
        )

        messages = [
            {"role": "system", "content": "Jesteś ekspertem weryfikującym czytelność zrzutów ekranu. Odpowiadaj TYLKO JSON."},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(self.max_retries + 1):
            try:
                content = self.provider.chat_completion(
                    messages=messages,
                    model=self.model,
                    temperature=0.2,
                    max_tokens=500,
                    json_mode=True,
                )
                parsed = robust_json_decode(content)
                verdict = str(parsed.get("verdict", "unknown")).strip().lower()
                reason = str(parsed.get("reason", "")).strip()
                if verdict not in ("ok", "unclear", "wrong"):
                    verdict = "unknown"
                return {"verdict": verdict, "reason": reason or "Brak powodu"}
            except Exception as e:
                if attempt >= self.max_retries:
                    return {"verdict": "unknown", "reason": f"Błąd sieci/JSON: {e}"}
                continue

        return {"verdict": "unknown", "reason": "Nieznany błąd"}


def improve_step_frame(
    video_path: Path,
    step: ManualStep,
    out_dir: Path,
    reviewer: Optional[FrameReviewer] = None,
) -> Optional[Path]:
    """
    Wycina kandydatów z okna kroku, rankuje ich po ostrości (Laplacian variance),
    a następnie recenzent LLM weryfikuje ich od najostrzejszego.
    """
    if reviewer is None:
        reviewer = FrameReviewer()

    duration = step.end_time - step.start_time
    if duration <= 0:
        return None

    candidates = []
    try:
        from vtd.core.frame_sharpness import extract_sharp_candidates, rank_sharp_frames

        cand_paths = extract_sharp_candidates(
            video_path,
            step.start_time,
            step.end_time,
            out_dir,
            step_number=step.step_number,
            k=4,
        )
        ranked = rank_sharp_frames(cand_paths)
        candidates = [p for _score, p in ranked]
    except (ImportError, Exception):
        # Fallback bez numpy: wycięcia w 70% i 40% trwania kroku
        for pct in [0.7, 0.4]:
            t = step.start_time + duration * pct
            fname = f"grid_retry_{step.step_number:04d}_{int(pct*100):02d}.png"
            out_path = out_dir / fname
            counter = 1
            while out_path.exists():
                out_path = out_dir / f"grid_retry_{step.step_number:04d}_{int(pct*100):02d}_{counter}.png"
                counter += 1
            from vtd.core.frame_extractor import extract_frame_at

            res = extract_frame_at(video_path, t, out_path)
            if res is None or not res.exists():
                continue
            candidates.append(res)

    for res in candidates:
        review = reviewer.review_step_frame(
            step.step_number, f"Krok {step.step_number}", step.speech_text or "", res
        )
        verdict = review.get("verdict", "unknown")
        if verdict == "ok":
            return res
        if candidates.index(res) + 1 >= reviewer.max_retries:
            break

    if candidates:
        return candidates[-1]
    return None
