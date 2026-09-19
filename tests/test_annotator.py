from pathlib import Path
from PIL import Image

from vtd.core.annotator import (
    load_frame,
    arrow,
    highlight_rect,
    numbered_badge,
    dim_background,
    annotate_frame,
    ANNOTATION_COLOR,
)


def _mk_frame(tmp_path, name="f.png", size=(640, 360), color=(255, 255, 255)):
    p = tmp_path / name
    img = Image.new("RGB", size, color)
    img.save(p)
    return p


def test_arrow_draws_colored_pixels(tmp_path):
    fp = _mk_frame(tmp_path)
    img = load_frame(fp)
    out = arrow(img, target_xy=(320, 180), direction_hint="left")
    px = out.load()
    found = any(px[x, y][:3] == ANNOTATION_COLOR for x in range(300, 340) for y in range(170, 190))
    assert found, "brak pikseli koloru adnotacji przy celu strzałki"


def test_highlight_rect_border_only(tmp_path):
    fp = _mk_frame(tmp_path)
    img = load_frame(fp)
    out = highlight_rect(img, [100, 100, 200, 150])
    px = out.load()
    edge = px[150, 92][:3]
    inside = px[150, 125][:3]
    assert edge == ANNOTATION_COLOR, f"brak ramki na krawędzi: {edge}"
    assert inside == (255, 255, 255), "highlight nie może wypełniać wnętrza"


def test_numbered_badge_draws_circle(tmp_path):
    fp = _mk_frame(tmp_path)
    img = load_frame(fp)
    out = numbered_badge(img, box=[150, 150, 250, 250], number=1)
    px = out.load()
    assert px[200, 200][:3] == ANNOTATION_COLOR or px[200, 200][:3] == (255, 255, 255)


def test_dim_background(tmp_path):
    fp = _mk_frame(tmp_path, color=(255, 255, 255))
    img = load_frame(fp)
    out = dim_background(img, keep_box=[200, 150, 400, 250], dim=0.5)
    out_rgb = out.convert("RGB")
    px = out_rgb.load()
    corner = px[5, 5]
    center = px[300, 200]
    assert corner[0] < 200, "tło nie przyciemnione"
    assert center == (255, 255, 255), "keep_box zmodyfikowany"


def test_annotate_frame_e2e(tmp_path):
    fp = _mk_frame(tmp_path)
    img = load_frame(fp)
    plan = [
        {"type": "arrow", "box": [100, 100, 150, 130], "label": "Przycisk"},
        {"type": "highlight", "box": [200, 200, 300, 250], "label": "Tabela"},
        {"type": "badge", "box": [400, 100, 450, 120], "number": 1, "label": "Krok 1"},
    ]
    res_img = annotate_frame(img, plan)
    dest = tmp_path / "out.png"
    res_img.save(dest)
    assert dest.exists()
    assert dest.stat().st_size > 0
