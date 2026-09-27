from pathlib import Path
from PIL import Image

from vtd.core.fusion import ManualStep
from vtd.builders.manual_builder import render_markdown_manual
from vtd.builders.html_builder import render_html_manual


def test_markdown_and_html_builders(tmp_path):
    frame_p = tmp_path / "f1.png"
    Image.new("RGB", (100, 100), (200, 200, 200)).save(frame_p)

    step = ManualStep(
        step_number=1,
        start_time=0.0,
        end_time=10.0,
        frame_path=frame_p,
        speech_text="Otwórz konfigurację",
    )

    md_content = render_markdown_manual(
        title="Test Instrukcja",
        steps=[step],
        intro="Wstęp testowy",
        metadata={"client": "TestClient", "document_status": "DRAFT"}
    )
    md_out = tmp_path / "MANUAL.md"
    md_out.write_text(md_content, encoding="utf-8")
    assert md_out.exists()
    content = md_out.read_text(encoding="utf-8")
    assert "Test Instrukcja" in content
    assert "Otwórz konfigurację" in content
    assert "DRAFT" in content

    html_out = tmp_path / "MANUAL.html"
    render_html_manual(
        title="Test Instrukcja",
        steps=[step],
        output_html=html_out,
        intro="Wstęp testowy",
        metadata={"client": "TestClient", "document_status": "DRAFT"}
    )
    assert html_out.exists()
    html_content = html_out.read_text(encoding="utf-8")
    assert "<html" in html_content
    assert "Test Instrukcja" in html_content


def test_gf001_regression_html_escaped(tmp_path):
    """GF-001: tytuł/ASR z `<script>` NIE trafia raw do INSTRUKCJA.html (XSS)."""
    from vtd.builders.html_builder import render_html_manual
    from vtd.core.fusion import ManualStep

    frame = tmp_path / "scene_0001.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    evil = '<script>alert(1)</script>'
    steps = [ManualStep(step_number=1, start_time=0.0, end_time=5.0,
                        frame_path=frame, speech_text=f"Opis {evil}")]
    enriched = {"steps": [{"step_number": 1, "title": evil, "description": evil,
                           "callout_type": "warning", "callout_text": evil}],
                "intro": evil}
    out = render_html_manual(title=evil, steps=steps, output_html=tmp_path / "INSTRUKCJA.html",
                             metadata={"client": evil}, enriched_data=enriched)
    html_text = Path(out).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text
