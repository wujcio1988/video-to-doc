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
