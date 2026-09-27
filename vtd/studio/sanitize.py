"""
Sanityzacja HTML z Markdownu (VTD-007) — bezpieczne renderowanie treści ASR/LLM.
"""
from __future__ import annotations

import html

import bleach
import markdown

_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "div", "span",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "a", "img",
    "details", "summary",
]

_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["align", "colspan", "rowspan"],
    "td": ["align", "colspan", "rowspan"],
    "code": ["class"],
    "div": ["class"],
    "span": ["class"],
}

_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_markdown_safe(text: str) -> str:
    """Konwertuje Markdown na HTML i sanityzuje wynik (ochrona przed stored XSS)."""
    if not text:
        return ""
    try:
        raw_html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        raw_html = f"<pre>{html.escape(text)}</pre>"
    return bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
