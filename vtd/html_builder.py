import base64
from pathlib import Path
from typing import List, Optional, Dict, Any
from vtd.fusion import ManualStep

def render_html_manual(
    title: str,
    steps: List[ManualStep],
    output_html: Path,
    intro: str = "",
    metadata: Optional[dict] = None,
    enriched_data: Optional[Dict[str, Any]] = None,
    mode: str = "manual",
) -> Path:
    """
    Generuje HTML — manual-first, status DRAFT / DO WERYFIKACJI, nigdy FINAL.
    W nagłówku metadane oraz informacja o źródłach. Dowód per krok: timestamp + klatka.
    """
    if mode == "meeting":
        from vtd.meeting_v2 import render_meeting_html_v2
        return render_meeting_html_v2(title, enriched_data, output_html, base_dir=output_html.parent)

    # Nigdy FINAL
    safe_metadata = dict(metadata) if metadata else {}
    for k, v in list(safe_metadata.items()):
        if str(v).upper() == "FINAL":
            safe_metadata[k] = "DRAFT"
    # Upewnij się że status DRAFT
    if "document_status" not in safe_metadata and "Status" not in safe_metadata:
        safe_metadata["Status"] = "DRAFT / DO WERYFIKACJI"
    # Dodaj info o źródłach / trybie
    safe_metadata["_sources"] = "narration / transcript / OCR / reference material / model suggestion"
    safe_metadata["_note"] = "Brak danych = DO POTWIERDZENIA / DO UZUPEŁNIENIA"

    meta_html = ""
    if safe_metadata:
        meta_items = []
        # Tylko czytelnem etadane client-ready: bez surowych dict/list
        readable_keys = ["client", "process", "module", "environment", "author", "document_status", "mode", "output_mode", "Status", "video_file", "generated_at", "pipeline", "title", "note"]
        # czytelne metadane typu client/process/...
        for k in readable_keys:
            if k in safe_metadata and safe_metadata[k] is not None:
                v = safe_metadata[k]
                if isinstance(v, (dict, list)):
                    continue
                # skip empty
                if str(v).strip() == "":
                    continue
                meta_items.append(f"<div><strong>{k}:</strong> {v}</div>")
        # stats -> krótki wiersz
        stats = safe_metadata.get("stats")
        if isinstance(stats, dict):
            seg = stats.get("segments", "-")
            # frames dedup preferred
            fr = stats.get("frames_dedup", stats.get("frames_raw", "-"))
            st = stats.get("steps", "-")
            meta_items.append(f"<div>Segmenty mowy: {seg} &bull; Klatki: {fr} &bull; Kroki: {st}</div>")
        # track_auto_detection -> czytelna linia
        tad = safe_metadata.get("track_auto_detection")
        if isinstance(tad, dict):
            has_speech = tad.get("has_speech")
            idx = tad.get("best_track_index")
            label = tad.get("best_track_label", "")
            if has_speech and idx is not None:
                meta_items.append(f"<div>Narracja: Track {idx+1} ({label}) &mdash; mowa wykryta.</div>")
            elif has_speech is False or idx is None:
                meta_items.append(f"<div>Narracja: nie wykryta &mdash; dokument z klatek (OCR).</div>")
            else:
                # fallback: if has_speech truthy but no index
                if has_speech:
                    meta_items.append(f"<div>Narracja: Track {label} &mdash; mowa wykryta.</div>")
                else:
                    meta_items.append(f"<div>Narracja: nie wykryta &mdash; dokument z klatek (OCR).</div>")
        # Zawsze pokaż źródła
        meta_items.append(f"<div style='grid-column:1/-1; font-size:12px; color:#64748b;'><em>Źródła: narration / transcript / OCR / reference material / model suggestion — brak danych = DO POTWIERDZENIA</em></div>")
        meta_html = f"<div class=\"meta-grid\">{''.join(meta_items)}</div>"

    badge_text = "Protokół Spotkania — DRAFT / DO WERYFIKACJI" if mode == "meeting" else "Dokumentacja Powdrożeniowa ERP — DRAFT / DO WERYFIKACJI"

    enriched_steps_map = {}
    if enriched_data and "steps" in enriched_data:
        for es in enriched_data["steps"]:
            if "step_number" in es:
                enriched_steps_map[es["step_number"]] = es

    step_cards = []
    for s in steps:
        es_info = enriched_steps_map.get(s.step_number, {})
        raw_title = es_info.get("title")
        if mode == "meeting":
            if raw_title:
                step_title = raw_title
            else:
                step_title = f"Temat {s.step_number}"
            caption_text = f"Rys. {s.step_number}. {raw_title}" if raw_title else f"Rys. {s.step_number}."
        else:
            if raw_title:
                step_title = raw_title
            else:
                step_title = f"Krok {s.step_number}"
            caption_text = f"Rys. {s.step_number}. {raw_title}" if raw_title else f"Rys. {s.step_number}."
        speech_desc = es_info.get("description") or s.speech_text or "Zmiana stanu ekranu zarejestrowana na nagraniu wideo. DO POTWIERDZENIA."

        img_tag = ""
        # Prefer frames_enhanced (zaakceptowane AI) -> frames_annotated -> frames (hierarchia frame_sources)
        frame_for_html = s.frame_path
        try:
            from vtd.frame_sources import resolve_frame as _rf
            _resolved_html = _rf(output_html.parent, s.frame_path.name)
            if _resolved_html is not None:
                frame_for_html = _resolved_html
        except Exception:
            pass
        if frame_for_html.exists():
            try:
                b64_data = base64.b64encode(frame_for_html.read_bytes()).decode("ascii")
                label = "Temat" if mode == "meeting" else "Krok"
                legend_html = ""
                if es_info.get("annotation_legend"):
                    legend_html = f"<div style='font-size:12px; color:#64748b; margin-top:4px; font-style:italic;'>{es_info.get('annotation_legend')}</div>"
                elif es_info.get("annotations"):
                    parts = []
                    for a in es_info.get("annotations", []):
                        if a.get("type") == "badge" and "number" in a:
                            parts.append(f"{a['number']} — {a.get('label', a.get('element',''))}")
                    if parts:
                        legend_html = f"<div style='font-size:12px; color:#64748b; margin-top:4px; font-style:italic;'>{' • '.join(parts)}</div>"
                img_tag = f"""
                <div class="step-image-container">
                    <img src="data:image/png;base64,{b64_data}" alt="{label} {s.step_number}">
                    <div class="caption">{caption_text}</div>
                    {legend_html}
                </div>
                """
            except Exception:
                pass
        # grid frames
        grid_html = ""
        if getattr(s, 'grid_frames', None):
            for gf in s.grid_frames:
                gf_for_html = gf
                try:
                    from vtd.frame_sources import resolve_frame as _rf_g
                    _rg = _rf_g(output_html.parent, gf.name)
                    if _rg is not None:
                        gf_for_html = _rg
                except Exception:
                    pass
                if gf_for_html.exists():
                    try:
                        b64_g = base64.b64encode(gf_for_html.read_bytes()).decode("ascii")
                        grid_html += f"""
                <div class="step-image-container" style="border:1px dashed #f59e0b;">
                    <img src="data:image/png;base64,{b64_g}" alt="grid {s.step_number}">
                    <div class="caption">Rys. {s.step_number} grid: klatka kontrolna (środek kroku)</div>
                </div>
                """
                    except Exception:
                        pass
        img_tag = img_tag + grid_html

        # QA preview
        qa_html = ""
        if es_info.get("qa_preview"):
            qa_html = f"""
            <div style="background:#fef2f2; border-left:4px solid #ef4444; padding:12px; margin-top:12px; border-radius:4px; font-size:13.5px;">
                <strong>WYMAGA PODGLĄDU RĘCZNEGO:</strong> zrzut może nie pokazywać opisanego elementu ({es_info.get('qa_preview')})
            </div>
            """

        callout_html = ""
        c_type = es_info.get("callout_type")
        c_text = es_info.get("callout_text")
        if c_type and c_text:
            c_bg = "#fef3c7" if c_type == "warning" else ("#ecfdf5" if c_type == "tip" else "#eff6ff")
            c_border = "#f59e0b" if c_type == "warning" else ("#10b981" if c_type == "tip" else "#3b82f6")
            c_icon = "⚠️" if c_type == "warning" else "💡"
            c_title = es_info.get("callout_title") or ("Uwaga" if c_type == "warning" else "Wskazówka")
            callout_html = f"""
            <div style="background:{c_bg}; border-left: 4px solid {c_border}; padding: 12px; margin-top: 12px; border-radius: 4px; font-size: 13.5px;">
                <strong>{c_icon} {c_title}:</strong> {c_text}
            </div>
            """

        card = f"""
        <article class="step-card">
            <div class="step-header">
                <div class="step-number">{s.step_number}</div>
                <div class="step-title">{step_title}</div>
            </div>
            <div class="step-description">
                <strong>{'Temat' if mode=='meeting' else 'Opis operacji'}:</strong> {speech_desc}
            </div>
            {img_tag}
            {callout_html}
            {qa_html}
        </article>
        """
        step_cards.append(card)

    final_intro = intro
    if enriched_data and enriched_data.get("intro"):
        final_intro = enriched_data["intro"]

    intro_label = "Cel spotkania" if mode == "meeting" else "Cel procedury"
    intro_box = ""
    if final_intro:
        intro_box = f"""
        <div class="intro-box">
            <strong>{intro_label}:</strong> {final_intro}
        </div>
        """
    elif mode == "manual":
        intro_box = f"""
        <div class="intro-box">
            <strong>{intro_label}:</strong> DO POTWIERDZENIA — cel do weryfikacji przez wdrożeniowca.
        </div>
        """
    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} — DRAFT</title>
    <style>
        :root {{
            --primary: #0284c7;
            --primary-dark: #0369a1;
            --primary-light: #e0f2fe;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --bg-page: #f8fafc;
            --bg-card: #ffffff;
            --border: #e2e8f0;
            --info-bg: #eff6ff;
            --info-border: #3b82f6;
            --info-text: #1e40af;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-page);
            color: var(--text-main);
            line-height: 1.6;
            padding: 24px 16px;
        }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        header {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px 28px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .badge {{
            display: inline-block;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 4px 10px;
            border-radius: 6px;
            background: var(--primary-light);
            color: var(--primary-dark);
            margin-bottom: 12px;
        }}
        h1 {{ font-size: 24px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
            font-size: 13px;
            color: var(--text-muted);
        }}
        .meta-grid strong {{ color: var(--text-main); }}
        .intro-box {{
            background: var(--info-bg);
            border-left: 4px solid var(--info-border);
            color: var(--info-text);
            padding: 16px;
            border-radius: 0 8px 8px 0;
            margin-bottom: 24px;
            font-size: 14px;
        }}
        .step-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.04);
        }}
        .step-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
        .step-number {{
            width: 32px;
            height: 32px;
            background: var(--primary);
            color: #ffffff;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 15px;
            flex-shrink: 0;
        }}
        .step-title {{ font-size: 18px; font-weight: 600; color: #0f172a; }}
        .step-description {{ font-size: 14.5px; margin-bottom: 14px; color: #334155; }}
        .step-image-container {{
            margin: 14px 0;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border);
            background: #f1f5f9;
        }}
        .step-image-container img {{ width: 100%; height: auto; display: block; }}
        .caption {{
            font-size: 12px;
            color: var(--text-muted);
            padding: 8px 12px;
            background: #f8fafc;
            border-top: 1px solid var(--border);
        }}
        footer {{
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border);
        }}
    </style>
</head>
<body>
<div class="container">
    <header>
        <div class="badge">{badge_text}</div>
        <h1>{title}</h1>
        {meta_html}
        <div style="margin-top:12px; font-size:12px; color:#b91c1c;"><strong>Status:</strong> DRAFT / DO WERYFIKACJI — materiał lokalny do ręcznej weryfikacji, nie wysłany do klienta.</div>
    </header>
    {intro_box}
    {''.join(step_cards)}
    <footer>
        Wygenerowano automatycznie przez Hermes Video-to-Manual Pipeline • DRAFT / DO WERYFIKACJI — materiał lokalny, nie wysłany do klienta. Źródła: narration/transcript/OCR/reference material/model suggestion • NVIDIA RTX 5070
    </footer>
</div>
</body>
</html>
"""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")
    return output_html
