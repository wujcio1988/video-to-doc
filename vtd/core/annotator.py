"""Deterministyczny silnik adnotacji na zrzutach — rysuje, nie generuje."""
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# Styl globalny
ANNOTATION_COLOR = (220, 38, 38)  # RED
# alias dla wygody
RED = ANNOTATION_COLOR

def _clamp_box(box: List[int], w: int, h: int) -> Optional[List[int]]:
    if not box or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = [int(v) for v in box]
    except Exception:
        return None
    # ensure x1<=x2, y1<=y2
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    # clamp
    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(0, min(x2, w))
    y2 = max(0, min(y2, h))
    # ensure minimal size
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]

def _line_width_for_image(w: int) -> int:
    return max(3, w // 400)

def _clamp_point(x: int, y: int, w: int, h: int) -> Tuple[int, int]:
    return (max(0, min(x, w - 1)), max(0, min(y, h - 1)))

def load_frame(path: Path) -> Image.Image:
    """Wczytaj obraz jako RGBA copy."""
    p = Path(path)
    img = Image.open(str(p)).convert("RGBA")
    return img

def _get_font(size: int = 14):
    # Try common paths
    try:
        # DejaVu is present on Ubuntu
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except Exception:
        try:
            return ImageFont.load_default()
        except Exception:
            return None

def arrow(img: Image.Image, target_xy: Tuple[int, int], direction_hint: str = "auto", color: Tuple[int,int,int] = RED) -> Image.Image:
    """Strzalka z krawedzi kadru do punktu OBOK elementu (offset 12px). direction_hint ignorowany, wybiera najblizsza krawedz."""
    if img is None:
        return img
    out = img.copy().convert("RGBA")
    w, h = out.size
    if target_xy is None:
        return out
    try:
        tx, ty = int(target_xy[0]), int(target_xy[1])
    except Exception:
        return out
    # clamp target
    tx, ty = _clamp_point(tx, ty, w, h)
    # Determine closest edge
    distances = {
        "left": tx,
        "right": w - tx,
        "top": ty,
        "bottom": h - ty,
    }
    # auto: pick min
    if direction_hint not in ("left", "right", "top", "bottom"):
        closest = min(distances, key=lambda k: distances[k])
    else:
        closest = direction_hint

    # Start point on edge
    if closest == "left":
        sx, sy = 0, ty
    elif closest == "right":
        sx, sy = w - 1, ty
    elif closest == "top":
        sx, sy = tx, 0
    else:
        sx, sy = tx, h - 1

    # End point offset 12px from target towards start (so arrow does not cover UI)
    offset = 12
    # vector from start to target
    vx = tx - sx
    vy = ty - sy
    length = (vx*vx + vy*vy) ** 0.5
    if length < 1:
        return out
    # unit vector
    ux, uy = vx/length, vy/length
    # end point is target minus offset * unit
    ex = int(tx - ux * offset)
    ey = int(ty - uy * offset)
    ex, ey = _clamp_point(ex, ey, w, h)

    lw = _line_width_for_image(w)
    # for arrow thickness 4px spec but make proportional as well
    thickness = max(4, lw)

    draw = ImageDraw.Draw(out, "RGBA")
    # Draw line
    draw.line([(sx, sy), (ex, ey)], fill=color + (255,), width=thickness)

    # Triangular arrow head at (ex,ey) pointing to target direction
    # head size
    head_len = max(14, thickness * 4)
    head_width = max(10, thickness * 3)
    # perpendicular vector
    px, py = -uy, ux
    # tip is slightly beyond ex towards target
    tip_x = int(ex + ux * 6)
    tip_y = int(ey + uy * 6)
    tip_x, tip_y = _clamp_point(tip_x, tip_y, w, h)
    # base points
    bx1 = int(ex - ux * head_len * 0.7 + px * head_width * 0.7)
    by1 = int(ey - uy * head_len * 0.7 + py * head_width * 0.7)
    bx2 = int(ex - ux * head_len * 0.7 - px * head_width * 0.7)
    by2 = int(ey - uy * head_len * 0.7 - py * head_width * 0.7)
    draw.polygon([(tip_x, tip_y), (bx1, by1), (bx2, by2)], fill=color + (255,), outline=color + (255,))

    return out

def highlight_rect(img: Image.Image, box: List[int], color: Tuple[int,int,int] = RED, padding: int = 8) -> Image.Image:
    if img is None:
        return img
    out = img.copy().convert("RGBA")
    if not box:
        return out
    w, h = out.size
    cb = _clamp_box(box, w, h)
    if cb is None:
        return out
    x1, y1, x2, y2 = cb
    # padding
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)
    lw = _line_width_for_image(w)
    thickness = max(4, lw)
    radius = 10
    draw = ImageDraw.Draw(out, "RGBA")
    # rounded rectangle outline only (not filled)
    # Use draw.rounded_rectangle with outline
    try:
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, outline=color + (255,), width=thickness)
    except Exception:
        # fallback to rectangle
        draw.rectangle([x1, y1, x2, y2], outline=color + (255,), width=thickness)
    return out

def numbered_badge(img: Image.Image, box: List[int], number: int, color: Tuple[int,int,int] = RED) -> Image.Image:
    if img is None:
        return img
    out = img.copy().convert("RGBA")
    if not box:
        # still handle? need box for position; if empty return copy
        return out
    w, h = out.size
    cb = _clamp_box(box, w, h)
    if cb is None:
        return out
    x1, y1, x2, y2 = cb
    # position at LEFT-TOP corner offset -6,-6
    cx = x1 - 6
    cy = y1 - 6
    # badge is circle 22px diameter -> radius 11
    radius = 11
    # clamp center to be inside image with radius margin
    cx = max(radius, min(cx, w - radius))
    cy = max(radius, min(cy, h - radius))

    draw = ImageDraw.Draw(out, "RGBA")
    # circle
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    # white border contrast?
    # Draw white outline thicker then RED fill? Spec: contrasting border
    # We'll draw outer white circle then inner RED
    draw.ellipse([bbox[0]-2, bbox[1]-2, bbox[2]+2, bbox[3]+2], fill=(255,255,255,255))
    draw.ellipse(bbox, fill=color + (255,), outline=(255,255,255,255), width=1)

    # number text white bold centered
    try:
        font = _get_font(13)
    except Exception:
        font = None
    text = str(number)
    # measure text
    try:
        # modern Pillow: textbbox
        bbox_text = draw.textbbox((0,0), text, font=font)
        tw = bbox_text[2] - bbox_text[0]
        th = bbox_text[3] - bbox_text[1]
    except Exception:
        tw, th = (8, 10)
    tx = cx - tw // 2
    ty = cy - th // 2 - 1  # slight adjustment
    draw.text((tx, ty), text, fill=(255,255,255,255), font=font)

    return out

def dim_background(img: Image.Image, keep_box: List[int], dim: float = 0.45) -> Image.Image:
    if img is None:
        return img
    out = img.copy().convert("RGBA")
    if not keep_box:
        return out
    w, h = out.size
    cb = _clamp_box(keep_box, w, h)
    if cb is None:
        return out
    x1, y1, x2, y2 = cb
    # Create dim overlay: semi-transparent black
    # dim factor 0.45 -> overlay alpha = int(255*dim)
    alpha = int(255 * max(0.0, min(dim, 1.0)))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, alpha))
    # Create mask: white where dim, black where keep (transparent)
    # For soft transition: blur edges of mask by 6px
    mask = Image.new("L", (w, h), 255)  # 255 = apply dim
    # keep area = 0 (no dim)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rectangle([x1, y1, x2, y2], fill=0)
    # blur mask for soft edge
    try:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=6))
    except Exception:
        pass
    # Convert mask to alpha channel for overlay: need to handle per pixel
    # We'll create a new overlay with mask applied: where mask 0 -> alpha 0, 255 -> alpha, blurred in between
    # Build final image via composite
    # Create base dim image (black with alpha)
    # Use Image.composite approach: we need to overlay onto out where mask indicates
    # Simple: create overlay_with_mask = overlay with its alpha modulated by mask
    # Instead directly composite using alpha_composite with masked overlay
    overlay_arr = overlay.copy()
    # Replace overlay alpha with mask-based
    # overlay alpha currently uniform alpha; modulate by mask
    # mask L -> 0-255; final_alpha = alpha * mask/255
    # Apply via putting mask as alpha channel of overlay
    # Create new alpha channel
    overlay_alpha = overlay.split()[3]  # uniform
    # modulate
    # Use point or paste
    # Create new overlay2 where alpha = (mask/255)*alpha
    # Build by manipulating pixels via ImageChops or simple blend
    from PIL import ImageChops
    # mask is 0 where keep, 255 where dim. So effective alpha = alpha * mask/255
    # create image of uniform alpha
    # Use Image.new to create alpha modulation
    # Simplest: create overlay2 with same RGB but alpha = mask applied
    # Build alpha channel
    # Use mask to modulate
    # mask currently already blurred
    # We'll compute final_alpha = Image.eval(mask, lambda p: int(p * alpha / 255))
    try:
        final_alpha = mask.point(lambda p: int(p * alpha / 255))
        overlay.putalpha(final_alpha)
    except Exception:
        overlay.putalpha(alpha)
    # Composite overlay onto out
    out = Image.alpha_composite(out, overlay)
    return out

def annotate_frame(img: Image.Image, annotations: List[Dict[str, Any]]) -> Image.Image:
    """Glowna funkcja: aplikuje liste annotations w kolejnosci dim -> highlight -> arrow -> badge."""
    if img is None:
        return img
    if not annotations:
        return img.copy().convert("RGBA")
    out = img.copy().convert("RGBA")
    # sort by type priority
    order = {"dim": 0, "highlight": 1, "arrow": 2, "badge": 3}
    # Also filter empty? We'll sort
    try:
        sorted_anns = sorted(annotations, key=lambda a: order.get(str(a.get("type","")).lower(), 99))
    except Exception:
        sorted_anns = annotations

    for ann in sorted_anns:
        if not isinstance(ann, dict):
            continue
        t = str(ann.get("type", "")).lower().strip()
        box = ann.get("box")
        # box may be list/tuple
        if box is not None:
            try:
                box = [int(v) for v in box]
            except Exception:
                box = None
        if t == "dim":
            # dim expects keep_box
            keep = box if box is not None else ann.get("keep_box")
            if keep is None:
                continue
            dim_val = ann.get("dim", 0.45)
            try:
                dim_val = float(dim_val)
            except Exception:
                dim_val = 0.45
            out = dim_background(out, keep, dim=dim_val)
        elif t == "highlight":
            if box is None:
                continue
            out = highlight_rect(out, box, color=RED)
        elif t == "arrow":
            if box is None:
                continue
            w, h = out.size
            cb = _clamp_box(box, w, h)
            if cb is None:
                continue
            # target_xy is center of box
            cx = (cb[0] + cb[2]) // 2
            cy = (cb[1] + cb[3]) // 2
            out = arrow(out, (cx, cy), color=RED)
        elif t == "badge":
            if box is None:
                continue
            number = ann.get("number", 1)
            try:
                number = int(number)
            except Exception:
                number = 1
            out = numbered_badge(out, box, number, color=RED)
        else:
            # unknown type -> skip
            continue
    return out
