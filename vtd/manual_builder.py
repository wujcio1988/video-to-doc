from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from vtd.fusion import ManualStep
from vtd.transcriber import SpeechSegment


def _metadata_header_lines(metadata: Optional[Dict[str, Any]]) -> str:
    if not metadata:
        return ""
    # Format: > Klient: X | Proces: Y ... — widoczne w Markdown
    parts = []
    for k in ["client", "process", "module", "environment", "author", "document_status", "mode", "output_mode"]:
        if k in metadata and metadata[k]:
            label = {
                "client": "Klient",
                "process": "Proces",
                "module": "Moduł",
                "environment": "Środowisko",
                "author": "Autor",
                "document_status": "Status",
                "mode": "Tryb",
                "output_mode": "Output",
            }.get(k, k)
            parts.append(f"**{label}:** {metadata[k]}")
    # Zmień status FINAL na DRAFT — nigdy FINAL dla draftu
    line = " | ".join(parts) if parts else ""
    if line:
        return f"> {line}\n>\n> **Źródła:** narration / transcript / OCR / reference material / model suggestion\n> **Uwaga:** Brak danych oznaczono jako `DO POTWIERDZENIA` / `DO UZUPEŁNIENIA` — nie dopisano faktów.\n"
    return ""


def render_clean_transcript(
    title: str,
    transcripts: List[SpeechSegment],
    speaker_label: str = "Lektor",
    mode: str = "manual",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generuje czysty dokument Markdown z transkrypcją, znacznikami czasowymi i tekstem ciągłym.
    Meeting: oznacza Desktop/system audio jako niezweryfikowany rozmówca.
    """
    lines = [
        f"# Transkrypcja: {title}\n",
        f"> Dokument wygenerowany automatycznie przez pipeline Hermes Video-to-Manual (ASR: faster-whisper / NVIDIA CUDA).\n",
        f"> Status: DRAFT / DO WERYFIKACJI — wymaga ręcznej weryfikacji przez wdrożeniowca.\n",
    ]
    if metadata:
        mh = _metadata_header_lines(metadata)
        if mh:
            lines.append(mh)
    if mode == "meeting":
        lines.append("> **Tryb spotkania:** Track 2 = Wdrożeniowiec; Track 3 = Desktop/system audio — niezweryfikowany rozmówca (nie Klient).\n")
    lines.append("## Przebieg nagrania ze znacznikami czasu\n")
    if not transcripts:
        lines.append("_Brak zarejestrowanych segmentów mowy w nagraniu._\n")
    else:
        for seg in transcripts:
            m_s, s_s = int(seg.start // 60), int(seg.start % 60)
            m_e, s_e = int(seg.end // 60), int(seg.end % 60)
            ts = f"[{m_s:02d}:{s_s:02d} - {m_e:02d}:{s_e:02d}]"
            label = seg.speaker if seg.speaker else speaker_label
            # Dla meeting nie pozwól na "Klient / Rozmówca" bez weryfikacji
            if mode == "meeting" and label in ("Klient / Rozmówca", "Klient"):
                label = "Desktop/system audio — niezweryfikowany rozmówca"
            lines.append(f"**{ts} [{label}]:** {seg.text}\n")

        lines.append("## Tekst ciągły (do skopiowania / notatki)\n")
        continuous_text = " ".join(seg.text for seg in transcripts).strip()
        lines.append(continuous_text + "\n")

    lines.append("\n---\n*Źródła: transcript (ASR). Brak danych = DO POTWIERDZENIA.*\n")
    return "\n".join(lines)


def build_llm_prompt(steps: List[ManualStep], module_title: str, mode: str = "manual", metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Tworzy prompt dla Agenta LLM. Wymusza źródła i DO POTWIERDZENIA.
    """
    if mode == "meeting":
        prompt = f"""Jesteś technicznym pisarzem protokołów ze spotkań wdrożeniowych ERP.
Na podstawie transkrypcji i klatek stwórz protokół ze spotkania dla tematu: "{module_title}".

Wymagane sekcje protokołu:
- Podstawy spotkania (data, tytuł, status DRAFT)
- Uczestnicy (jeśli znani, inaczej DO UZUPEŁNIENIA; Track 3 = Desktop/system audio — niezweryfikowany rozmówca, nie Klient)
- Tematy / Agenda
- Decyzje
- Ustalenia
- Action items (owner/termin — jeśli brak DO POTWIERDZENIA)
- Pytania otwarte
- Ryzyka
- DO POTWIERDZENIA

Zasady:
- Każdy element oznacza źródłem: narration/transcript/OCR/reference material/model suggestion.
- Nie dopisuj faktów. Brak danych = DO POTWIERDZENIA.
- Track 3 nigdy nie jest Klientem bez weryfikacji.

Materiały wejściowe (z dowodami):
"""
    else:
        prompt = f"""Jesteś technicznym pisarzem dokumentacji powdrożeniowej systemów ERP.
Na podstawie transkrypcji mowy wdrożeniowca i zarejestrowanych klatek ekranu stwórz profesjonalną instrukcję krok po kroku w Markdown dla tematu: "{module_title}".

Zasady redagowania:
1. Każdy krok musi zawierać:
   - Tytuł kroku (czasownik w trybie rozkazującym)
   - Osadzenie zrzutu: `![Krok N](relatywna_sciezka)`
   - Zwięzły opis (max 2-3 zdania) + dowód: timestamp start/end + nazwa klatki + źródło (narration/transcript/OCR/reference material/model suggestion)
   - Sekcję 'Cel', 'Warunki wstępne' (jeśli brak = DO POTWIERDZENIA), 'Rezultat', 'Uwagi', 'DO POTWIERDZENIA' gdy brak danych
2. Usuń wtrącenia ('yyy').
3. Zachowaj zgodność z transkrypcją (nie wymyślaj kroków).
4. Status DRAFT — nigdy FINAL. Brak danych = DO POTWIERDZENIA / DO UZUPEŁNIENIA.

Materiały wejściowe do zredagowania (z dowodami):
"""
    if metadata:
        prompt += f"\nMetadane: {metadata}\n"
    for s in steps:
        prompt += f"\n--- KROK {s.step_number} (Znacznik: {s.start_time:.1f}s - {s.end_time:.1f}s | klatka: {s.frame_path.name} | źródło: narration/transcript) ---\n"
        prompt += f"Ścieżka klatki: {s.frame_path.as_posix()}\n"
        prompt += f"Wypowiedź lektora: {s.speech_text}\n"
        prompt += f"Dowód: klatka={s.frame_path.name}, timestamp={s.start_time:.1f}s - {s.end_time:.1f}s\n"
    return prompt


def _render_manual_markdown(title: str, steps: List[ManualStep], intro: str, base_dir: Optional[Path], enriched_data: Optional[Dict[str, Any]], metadata: Optional[Dict[str, Any]]) -> str:
    md = [f"# {title}\n"]
    if metadata:
        mh = _metadata_header_lines(metadata)
        if mh:
            md.append(mh)
        # Dodatkowa linia statusu
        status = metadata.get("document_status", "DRAFT")
        md.append(f"> **Status dokumentu:** {status} — materiał lokalny do ręcznej weryfikacji, nie wysłany do klienta.\n")
    if intro:
        md.append(f"{intro}\n")
    else:
        md.append("Dokumentacja wygenerowana automatycznie z materiału wideo przez Hermes Video-to-Manual Pipeline.\n")
        md.append("> **Źródła:** narration/transcript/OCR/reference material/model suggestion. Brak danych = DO POTWIERDZENIA.\n")

    # Metadane w nagłówku jeśli z enriched_data meta fallback
    if enriched_data and "meta" in enriched_data and not metadata:
        meta_lines = []
        for k, v in enriched_data["meta"].items():
            meta_lines.append(f"- **{k}:** {v}")
        if meta_lines:
            md.append("## Metadane\n")
            md.extend(meta_lines)
            md.append("")

    md.append("## Cel procedury\n")
    if enriched_data and enriched_data.get("intro"):
        md.append(f"{enriched_data['intro']} _(źródło: narration/transcript)_\n")
    else:
        md.append("DO POTWIERDZENIA — cel procedury do uzupełnienia na podstawie nagrania. _(źródło: DO POTWIERDZENIA)_\n")

    md.append("## Warunki wstępne\n")
    md.append("- DO POTWIERDZENIA — wymagane dostępy, role, dane testowe do uzupełnienia przez wdrożeniowca.\n")

    md.append("## Procedura postępowania krok po kroku\n")
    enriched_steps_map = {}
    if enriched_data and "steps" in enriched_data:
        for es in enriched_data["steps"]:
            if "step_number" in es:
                enriched_steps_map[es["step_number"]] = es
    for s in steps:
        es_info = enriched_steps_map.get(s.step_number, {})
        raw_title = es_info.get("title")
        if raw_title:
            step_title = raw_title
            caption = f"*Rys. {s.step_number}. {raw_title}*"
            heading = f"### Krok {s.step_number}: {step_title}\n"
        else:
            caption = f"*Rys. {s.step_number}.*"
            heading = f"### Krok {s.step_number}\n"
        step_desc = es_info.get("description") or s.speech_text or "Zmiana stanu ekranu zarejestrowana na wideo. _(źródło: narration)_"
        md.append(heading)
        # Prefer frames_enhanced (zaakceptowane AI) -> frames_annotated -> frames (hierarchia frame_sources)
        frame_to_use = s.frame_path
        if base_dir is not None:
            from vtd.frame_sources import resolve_frame
            _resolved = resolve_frame(base_dir, s.frame_path.name)
            if _resolved is not None:
                frame_to_use = _resolved
        if base_dir and frame_to_use.is_relative_to(base_dir):
            rel_frame = frame_to_use.relative_to(base_dir).as_posix()
        else:
            # fallback to frames_annotated relative if base_dir not available but file exists in CWD frames_annotated
            if (Path("frames_annotated") / s.frame_path.name).exists():
                rel_frame = f"frames_annotated/{s.frame_path.name}"
            else:
                rel_frame = f"frames/{s.frame_path.name}" if frame_to_use == s.frame_path else f"frames_annotated/{frame_to_use.name}"
        md.append(f"![Krok {s.step_number}]({rel_frame})\n")
        md.append(f"{caption}\n")
        # Legend for badges if present
        if es_info.get("annotation_legend"):
            md.append(f"*{es_info.get('annotation_legend')}*\n")
        elif es_info.get("annotations"):
            # build fallback legend from annotations
            parts = []
            for a in es_info.get("annotations", []):
                if a.get("type") == "badge" and "number" in a:
                    parts.append(f"{a['number']} — {a.get('label', a.get('element',''))}")
            if parts:
                md.append(f"*{' • '.join(parts)}*\n")
        md.append(f"**Opis operacji:** {step_desc}\n")
        if es_info.get("callout_text"):
            ct = es_info.get("callout_type", "tip")
            md.append(f"> **{es_info.get('callout_title','Uwaga')}** ({ct}): {es_info.get('callout_text')} _(źródło: {es_info.get('source','model suggestion')})_\n")
        # Grid frames additional evidence — prefer annotated
        if getattr(s, 'grid_frames', None):
            for gf in s.grid_frames:
                gf_to_use = gf
                if base_dir is not None:
                    from vtd.frame_sources import resolve_frame as _rf_g
                    _rg = _rf_g(base_dir, gf.name)
                    if _rg is not None:
                        gf_to_use = _rg
                if base_dir and gf_to_use.is_relative_to(base_dir):
                    rel_gf = gf_to_use.relative_to(base_dir).as_posix()
                else:
                    rel_gf = f"frames/{gf.name}" if gf_to_use == gf else f"frames_annotated/{gf_to_use.name}"
                md.append(f"![Krok {s.step_number} grid]({rel_gf})\n")
                md.append(f"*Rys. {s.step_number} grid: klatka kontrolna (środek kroku)*\n")
        # Frame QA Wymaga podglądu
        if es_info.get("qa_preview"):
            md.append(f"**Uwagi:** WYMAGA PODGLĄDU RĘCZNEGO: zrzut może nie pokazywać opisanego elementu ({es_info.get('qa_preview')}).\n")
        else:
            md.append(f"**Uwagi:** DO POTWIERDZENIA — ryzyka / wyjątki do potwierdzenia.\n")
        md.append(f"**Rezultat:** DO POTWIERDZENIA — oczekiwany stan po kroku do weryfikacji.\n")

    md.append("## Rezultat końcowy\n")
    md.append("DO POTWIERDZENIA — opis stanu docelowego do weryfikacji.\n")
    md.append("## Uwagi i DO POTWIERDZENIA\n")
    md.append("- Wszystkie kroki wymagają ręcznej weryfikacji przez wdrożeniowca. Status: DRAFT / DO WERYFIKACJI.\n")
    md.append("- Brakujące dane oznaczono jako DO POTWIERDZENIA / DO UZUPEŁNIENIA — nie dopisano faktów.\n")
    return "\n".join(md)


def _render_meeting_markdown(title: str, steps: List[ManualStep], intro: str, base_dir: Optional[Path], enriched_data: Optional[Dict[str, Any]], metadata: Optional[Dict[str, Any]]) -> str:
    md = [f"# Protokół spotkania: {title}\n"]
    if metadata:
        mh = _metadata_header_lines(metadata)
        if mh:
            md.append(mh)
        status = metadata.get("document_status", "DRAFT")
        md.append(f"> **Status protokołu:** {status} / DO WERYFIKACJI — draft lokalny, nie wysłany do klienta.\n")
    md.append(f"> **Data wygenerowania:** {datetime.now(timezone.utc).isoformat()} | **Źródła:** narration/transcript/OCR/reference material/model suggestion\n")
    if intro:
        md.append(f"{intro}\n")
    elif enriched_data and enriched_data.get("intro"):
        md.append(f"{enriched_data['intro']}\n")
    else:
        md.append("_DO POTWIERDZENIA — cel spotkania do uzupełnienia._\n")

    md.append("## Podstawy spotkania\n")
    md.append(f"- **Tytuł:** {title}\n")
    md.append(f"- **Data nagrania:** DO POTWIERDZENIA\n")
    md.append(f"- **Miejsce / kanał:** DO POTWIERDZENIA\n")
    md.append(f"- **Status:** DRAFT / DO WERYFIKACJI\n")

    md.append("## Uczestnicy\n")
    md.append("- **Wdrożeniowiec:** DO UZUPEŁNIENIA (Track 2)\n")
    md.append("- **Desktop/system audio — niezweryfikowany rozmówca:** Track 3 — nie traktować jako wypowiedzi osoby bez weryfikacji (nie Klient)\n")
    md.append("- **Inni uczestnicy:** DO POTWIERDZENIA\n")

    md.append("## Tematy / Agenda\n")
    enriched_steps_map = {}
    if enriched_data and "steps" in enriched_data:
        for es in enriched_data["steps"]:
            if "step_number" in es:
                enriched_steps_map[es["step_number"]] = es
    if steps:
        for s in steps:
            es_info = enriched_steps_map.get(s.step_number, {})
            raw_title = es_info.get("title")
            d = es_info.get("description") or s.speech_text or "DO POTWIERDZENIA"
            if raw_title:
                t = raw_title
                heading = f"### Temat {s.step_number}: {t}\n"
                caption = f"*Rys. {s.step_number}. {t}*"
            else:
                heading = f"### Temat {s.step_number}\n"
                caption = f"*Rys. {s.step_number}.*"
            md.append(heading)
            md.append(f"- **Omówienie:** {d}\n")
            if s.frame_path.exists():
                if base_dir and s.frame_path.is_relative_to(base_dir):
                    rel = s.frame_path.relative_to(base_dir).as_posix()
                else:
                    rel = f"frames/{s.frame_path.name}"
                md.append(f"  ![Temat {s.step_number}]({rel})\n")
                md.append(f"  {caption}\n")
            else:
                md.append(f"- **Dowód obrazu:** DO POTWIERDZENIA — brak pliku `{s.frame_path.name}`.\n")
    else:
        md.append("- DO POTWIERDZENIA — brak tematów do uzupełnienia.\n")

    # Sekcje wynikowe muszą pochodzić z modelu; bez enrichera zostaje jawny placeholder.
    def _items(key: str, empty: str) -> list:
        value = enriched_data.get(key, []) if enriched_data else []
        return value if isinstance(value, list) else []

    md.append("## Decyzje\n")
    decisions = _items("decisions", "")
    if decisions:
        for d in decisions:
            if isinstance(d, dict):
                text = d.get("decision") or d.get("text") or "DO POTWIERDZENIA"
                ev = d.get("evidence", {}) or {}
                md.append(f"- **{text}** — uzasadnienie: {d.get('rationale', 'DO POTWIERDZENIA')} [źródło: {d.get('source', 'transcript')}; timestamp: {ev.get('timestamp', 'DO POTWIERDZENIA')}]\n")
            else:
                md.append(f"- {d}\n")
    else:
        md.append("- DO POTWIERDZENIA — nie wykryto zweryfikowanych decyzji.\n")

    md.append("## Ustalenia\n")
    agreements = _items("agreements", "")
    if agreements:
        for a in agreements:
            if isinstance(a, dict):
                md.append(f"- {a.get('text') or a.get('agreement') or 'DO POTWIERDZENIA'} [źródło: {a.get('source', 'transcript')}]\n")
            else:
                md.append(f"- {a}\n")
    else:
        md.append("- DO POTWIERDZENIA — nie wykryto zweryfikowanych ustaleń.\n")

    md.append("## Action items\n")
    md.append("| # | Zadanie | Owner | Termin | Źródło | Dowód |\n")
    md.append("|---|---------|-------|--------|--------|-------|\n")
    actions = _items("action_items", "")
    if actions:
        for i, a in enumerate(actions, 1):
            if not isinstance(a, dict):
                a = {"task": str(a)}
            ev = a.get("evidence", {}) or {}
            md.append(f"| {i} | {a.get('task', 'DO POTWIERDZENIA')} | {a.get('owner', 'DO POTWIERDZENIA')} | {a.get('due', 'DO POTWIERDZENIA')} | {a.get('source', 'transcript')} | {ev.get('timestamp', 'DO POTWIERDZENIA')} |\n")
    else:
        md.append("| 1 | DO POTWIERDZENIA | DO UZUPEŁNIENIA | DO POTWIERDZENIA | DO POTWIERDZENIA | — |\n")

    md.append("## Pytania otwarte\n")
    questions = _items("open_questions", "")
    md.extend([f"- {(q.get('text') or q.get('question') or 'DO POTWIERDZENIA') if isinstance(q, dict) else q}\n" for q in questions] or ["- DO POTWIERDZENIA\n"])
    md.append("## Ryzyka\n")
    risks = _items("risks", "")
    md.extend([f"- {(r.get('text') or r.get('risk') or 'DO POTWIERDZENIA') if isinstance(r, dict) else r}\n" for r in risks] or ["- DO POTWIERDZENIA\n"])
    md.append("## DO POTWIERDZENIA — sekcja zbiorcza\n")
    md.append("- Wszystkie informacje wymagają potwierdzenia z uczestnikami. Dokument DRAFT, nie wysłany do klienta.\n")
    return "\n".join(md)


def render_markdown_manual(title: str, steps: List[ManualStep], intro: str = "", base_dir: Optional[Path] = None, enriched_data: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None, mode: str = "manual") -> str:
    """
    Generuje szkielet dokumentu Markdown. Rozdziela kontrakty manual vs meeting.
    """
    if mode == "meeting":
        from vtd.meeting_v2 import render_meeting_markdown_v2
        return render_meeting_markdown_v2(title, enriched_data, base_dir=base_dir)
    return _render_manual_markdown(title, steps, intro, base_dir, enriched_data, metadata)


def render_docx_manual(
    title: str,
    steps: List[ManualStep],
    output_docx: Path,
    intro: str = "",
    enriched_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    mode: str = "manual",
) -> Path:
    """
    Generuje dokument Word. Wspiera metadane i meeting vs manual.
    """
    try:
        import docx
        from docx.shared import Inches, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement, parse_xml
        from docx.oxml.ns import nsdecls, qn
    except ImportError:
        return output_docx

    doc = docx.Document()

    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    COLOR_PRIMARY = RGBColor(2, 132, 199)
    COLOR_DARK = RGBColor(15, 23, 42)
    COLOR_MUTED = RGBColor(100, 116, 139)
    COLOR_SUCCESS = RGBColor(6, 95, 70)
    COLOR_WARNING = RGBColor(146, 64, 14)

    def set_cell_background(cell, fill_hex):
        shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>')
        cell._tc.get_or_add_tcPr().append(shading_elm)

    def set_cell_margins(cell, top=100, bottom=100, left=140, right=140):
        tcPr = cell._tc.get_or_add_tcPr()
        tcMar = OxmlElement('w:tcMar')
        for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
            node = OxmlElement(f'w:{m}')
            node.set(qn('w:w'), str(val))
            node.set(qn('w:type'), 'dxa')
            tcMar.append(node)
        tcPr.append(tcMar)

    tp = doc.add_paragraph()
    tp.paragraph_format.space_before = Pt(0)
    tp.paragraph_format.space_after = Pt(2)
    if mode == "meeting":
        r_badge = tp.add_run("PROTOKÓŁ SPOTKANIA — DRAFT / DO WERYFIKACJI\n")
    else:
        r_badge = tp.add_run("DOKUMENTACJA POWDROŻENIOWA ERP — DRAFT / DO WERYFIKACJI\n")
    r_badge.font.size = Pt(9.5)
    r_badge.font.bold = True
    r_badge.font.color.rgb = COLOR_PRIMARY

    r_title = tp.add_run(title)
    r_title.font.size = Pt(18)
    r_title.font.bold = True
    r_title.font.color.rgb = COLOR_DARK

    # Metadane w tabeli
    meta_to_show: Dict[str, Any] = {}
    if metadata:
        meta_to_show = {k: v for k, v in metadata.items() if v}
    elif enriched_data and "meta" in enriched_data:
        meta_to_show = enriched_data["meta"]
    if meta_to_show:
        # Upewnij się że nie ma FINAL
        for k in list(meta_to_show.keys()):
            if str(meta_to_show[k]).upper() == "FINAL":
                meta_to_show[k] = "DRAFT"
        m_items = list(meta_to_show.items())
        meta_pairs = []
        for i in range(0, len(m_items), 2):
            p1 = m_items[i]
            p2 = m_items[i+1] if i+1 < len(m_items) else ("", "")
            meta_pairs.append([(f"{p1[0]}:", f" {p1[1]}"), (f"{p2[0]}:", f" {p2[1]}")])
        if meta_pairs:
            from docx.enum.table import WD_TABLE_ALIGNMENT
            meta_tbl = doc.add_table(rows=len(meta_pairs), cols=2)
            meta_tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            for r_i, r_pairs in enumerate(meta_pairs):
                for c_i, (k_lbl, v_lbl) in enumerate(r_pairs):
                    if k_lbl:
                        m_cell = meta_tbl.cell(r_i, c_i)
                        set_cell_background(m_cell, "F8FAFC")
                        set_cell_margins(m_cell, top=60, bottom=60, left=100, right=100)
                        mp = m_cell.paragraphs[0]
                        mp.paragraph_format.space_after = Pt(0)
                        rk = mp.add_run(k_lbl)
                        rk.bold = True
                        rk.font.size = Pt(9.0)
                        rv = mp.add_run(v_lbl)
                        rv.font.size = Pt(9.0)
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
            # Dodatkowa informacja o źródłach
            sp = doc.add_paragraph()
            sr = sp.add_run("Źródła: narration / transcript / OCR / reference material / model suggestion — brak danych = DO POTWIERDZENIA")
            sr.font.size = Pt(8.5)
            sr.font.color.rgb = COLOR_MUTED
            sr.font.italic = True

    doc_intro = intro
    if enriched_data and enriched_data.get("intro"):
        doc_intro = enriched_data["intro"]

    if doc_intro:
        box_table = doc.add_table(rows=1, cols=1)
        b_cell = box_table.cell(0, 0)
        set_cell_background(b_cell, "EFF6FF")
        set_cell_margins(b_cell, top=120, bottom=120, left=160, right=160)
        bp = b_cell.paragraphs[0]
        bp.paragraph_format.space_after = Pt(0)
        if mode == "meeting":
            rb = bp.add_run("Cel spotkania: ")
        else:
            rb = bp.add_run("Cel procedury: ")
        rb.bold = True
        rb.font.color.rgb = COLOR_PRIMARY
        bp.add_run(doc_intro)
        doc.add_paragraph().paragraph_format.space_after = Pt(8)
    elif mode == "manual":
        box_table = doc.add_table(rows=1, cols=1)
        b_cell = box_table.cell(0, 0)
        set_cell_background(b_cell, "EFF6FF")
        set_cell_margins(b_cell, top=120, bottom=120, left=160, right=160)
        bp = b_cell.paragraphs[0]
        bp.paragraph_format.space_after = Pt(0)
        rb = bp.add_run("Cel procedury: ")
        rb.bold = True
        rb.font.color.rgb = COLOR_PRIMARY
        bp.add_run("DO POTWIERDZENIA")
        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    if mode == "meeting":
        # Dla meeting dodaj sekcje protokołu skrótowo (szczegóły w Markdown)
        sec = doc.add_paragraph()
        sec.add_run("Protokół zawiera sekcje: Podstawy spotkania, Uczestnicy, Tematy, Decyzje, Ustalenia, Action items, Pytania otwarte, Ryzyka, DO POTWIERDZENIA. ").bold = True
        sec.add_run("Track 3 = Desktop/system audio — niezweryfikowany rozmówca (nie Klient). Szczegóły w pliku Markdown.")
        doc.add_paragraph().paragraph_format.space_after = Pt(8)

    enriched_steps_map = {}
    if enriched_data and "steps" in enriched_data:
        for es in enriched_data["steps"]:
            if "step_number" in es:
                enriched_steps_map[es["step_number"]] = es

    for s in steps:
        es_info = enriched_steps_map.get(s.step_number, {})
        if mode == "meeting":
            step_title = es_info.get("title") or f"Temat {s.step_number}"
        else:
            step_title = es_info.get("title") or f"Operacja ({s.start_time:.1f}s - {s.end_time:.1f}s)"
        step_desc = es_info.get("description") or s.speech_text or "Zmiana stanu ekranu zarejestrowana na wideo. DO POTWIERDZENIA."

        hp = doc.add_paragraph()
        hp.paragraph_format.space_before = Pt(14)
        hp.paragraph_format.space_after = Pt(4)
        if mode == "meeting":
            r_num = hp.add_run(f"Temat {s.step_number}. ")
        else:
            r_num = hp.add_run(f"Krok {s.step_number}. ")
        r_num.bold = True
        r_num.font.size = Pt(13)
        r_num.font.color.rgb = COLOR_PRIMARY
        r_t = hp.add_run(step_title)
        r_t.bold = True
        r_t.font.size = Pt(13)
        r_t.font.color.rgb = COLOR_DARK

        dp = doc.add_paragraph()
        dp.paragraph_format.space_after = Pt(4)
        r_desc_lbl = dp.add_run("Opis: " if mode == "meeting" else "Opis operacji: ")
        r_desc_lbl.bold = True
        dp.add_run(step_desc)

        # Dowód
        ev = doc.add_paragraph()
        ev.paragraph_format.space_after = Pt(6)
        er = ev.add_run(f"Dowód: klatka {s.frame_path.name} | {s.start_time:.1f}s - {s.end_time:.1f}s | źródło: narration/transcript")
        er.font.size = Pt(8.5)
        er.font.color.rgb = COLOR_MUTED
        er.font.italic = True

        # Prefer annotated frame if exists
        frame_for_doc = s.frame_path
        # try frames_annotated sibling: assume output_docx is in output_dir, so output_dir is parent
        try:
            candidate_annot = output_docx.parent / "frames_annotated" / s.frame_path.name
            if candidate_annot.exists():
                frame_for_doc = candidate_annot
        except Exception:
            pass
        if frame_for_doc.exists():
            try:
                doc.add_picture(str(frame_for_doc), width=Inches(6.2))
                cp = doc.add_paragraph()
                cp.paragraph_format.space_before = Pt(2)
                cp.paragraph_format.space_after = Pt(8)
                if mode == "meeting":
                    rc = cp.add_run(f"Rys. {s.step_number}: Stan ekranu dla tematu {s.step_number}.")
                else:
                    rc = cp.add_run(f"Rys. {s.step_number}: Stan ekranu zarejestrowany w kroku {s.step_number}.")
                rc.font.size = Pt(8.5)
                rc.font.italic = True
                rc.font.color.rgb = COLOR_MUTED
                # annotation legend under image
                if es_info.get("annotation_legend"):
                    lp = doc.add_paragraph()
                    lp.paragraph_format.space_after = Pt(4)
                    lr = lp.add_run(es_info.get("annotation_legend"))
                    lr.font.size = Pt(8.5)
                    lr.font.italic = True
                    lr.font.color.rgb = COLOR_MUTED
            except Exception:
                pass
        # Grid frames
        if getattr(s, 'grid_frames', None):
            for gf in s.grid_frames:
                gf_for_doc = gf
                try:
                    cand_g = output_docx.parent / "frames_annotated" / gf.name
                    if cand_g.exists():
                        gf_for_doc = cand_g
                except Exception:
                    pass
                if gf_for_doc.exists():
                    try:
                        doc.add_picture(str(gf_for_doc), width=Inches(6.2))
                        cp2 = doc.add_paragraph()
                        cp2.paragraph_format.space_before = Pt(2)
                        cp2.paragraph_format.space_after = Pt(8)
                        rc2 = cp2.add_run(f"Rys. {s.step_number} grid: klatka kontrolna (środek kroku).")
                        rc2.font.size = Pt(8.5)
                        rc2.font.italic = True
                        rc2.font.color.rgb = COLOR_MUTED
                    except Exception:
                        pass
        # QA preview
        if es_info.get("qa_preview"):
            qp = doc.add_paragraph()
            qp.paragraph_format.space_after = Pt(4)
            r_lbl = qp.add_run("Uwagi: ")
            r_lbl.bold = True
            qp.add_run(f"WYMAGA PODGLĄDU RĘCZNEGO: zrzut może nie pokazywać opisanego elementu ({es_info.get('qa_preview')}).")
        c_type = es_info.get("callout_type")
        c_text = es_info.get("callout_text")
        if c_type and c_text:
            c_tbl = doc.add_table(rows=1, cols=1)
            c_cell = c_tbl.cell(0, 0)
            fill_color = "FEF3C7" if c_type == "warning" else ("ECFDF5" if c_type == "tip" else ("F1F5F9" if c_type == "quote" else "EFF6FF"))
            set_cell_background(c_cell, fill_color)
            set_cell_margins(c_cell, top=100, bottom=100, left=140, right=140)
            callout_p = c_cell.paragraphs[0]
            callout_p.paragraph_format.space_after = Pt(0)
            c_lbl = es_info.get("callout_title") or ("Uwaga" if c_type == "warning" else ("Wskazówka" if c_type == "tip" else "Wskazówka wdrożeniowca z nagrania"))
            r_clbl = callout_p.add_run(f"{c_lbl}: ")
            r_clbl.bold = True
            if c_type == "warning":
                r_clbl.font.color.rgb = COLOR_WARNING
            elif c_type == "tip":
                r_clbl.font.color.rgb = COLOR_SUCCESS
            else:
                r_clbl.font.color.rgb = COLOR_PRIMARY
            callout_p.add_run(c_text)
            doc.add_paragraph().paragraph_format.space_after = Pt(6)

    footer_p = doc.add_paragraph()
    footer_p.paragraph_format.space_before = Pt(20)
    rf = footer_p.add_run("Wygenerowano automatycznie przez Hermes Video-to-Manual Pipeline • DRAFT / DO WERYFIKACJI — materiał lokalny, nie wysłany do klienta. Źródła: narration/transcript/OCR/reference/model suggestion.")
    rf.font.size = Pt(8.5)
    rf.font.color.rgb = COLOR_MUTED
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx))
    return output_docx
