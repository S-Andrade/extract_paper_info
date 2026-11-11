"""Read PDF text and locate quoted passages on the page."""

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Join words hyphenated across line breaks; expand ligatures (ﬁ -> fi); ignore off-page text.
TEXT_FLAGS = pymupdf.TEXT_DEHYPHENATE | pymupdf.TEXT_MEDIABOX_CLIP


@dataclass
class Match:
    page_index: int
    page_label: str
    rects: list[pymupdf.Rect]  # MuPDF coordinates (top-left origin), one per line
    text: str  # the text as it appears in the PDF
    char_offset: int  # position of the match within the page text, for sort order


def _norm(token: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", token).lower())


def _words(page: pymupdf.Page) -> list[tuple]:
    return [w for w in page.get_text("words", flags=TEXT_FLAGS, sort=False) if _norm(w[4])]


def page_texts(doc: pymupdf.Document) -> list[str]:
    """Plain text per page, built from the same words the matcher searches."""
    return [" ".join(w[4] for w in _words(page)) for page in doc]


def has_text_layer(doc: pymupdf.Document) -> bool:
    return sum(len(t) for t in page_texts(doc)) > 200 * min(len(doc), 3)


def _find_span(page_tokens: list[str], quote_tokens: list[str]) -> tuple[int, int] | None:
    """Return [start, end) indices of the quote in the page tokens, tolerating small differences."""
    n = len(quote_tokens)
    if n == 0:
        return None
    for i in range(len(page_tokens) - n + 1):
        if page_tokens[i : i + n] == quote_tokens:
            return i, i + n
    sm = difflib.SequenceMatcher(None, page_tokens, quote_tokens, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
    if not blocks:
        return None
    # Use the densest cluster of matching blocks: anchor on the longest block and
    # keep neighbours that fall within a quote-length window.
    anchor = max(blocks, key=lambda b: b.size)
    lo = anchor.a - anchor.b - n // 4
    hi = anchor.a + (n - anchor.b) + n // 4
    near = [b for b in blocks if b.a >= lo and b.a + b.size <= hi]
    matched = sum(b.size for b in near)
    if matched / n < 0.8:
        return None
    return min(b.a for b in near), max(b.a + b.size for b in near)


def locate(doc: pymupdf.Document, quote: str, page_hint: int | None) -> Match | None:
    """Find a quote in the document, trying the hinted page (0-based) and its neighbours first."""
    quote_tokens = [t for t in (_norm(w) for w in quote.split()) if t]
    order = list(range(len(doc)))
    if page_hint is not None and 0 <= page_hint < len(doc):
        order.sort(key=lambda i: abs(i - page_hint))
    for pi in order:
        page = doc[pi]
        words = _words(page)
        span = _find_span([_norm(w[4]) for w in words], quote_tokens)
        if span is None:
            continue
        hit = words[span[0] : span[1]]
        lines: dict[tuple[int, int], pymupdf.Rect] = {}
        for w in hit:
            key = (w[5], w[6])  # block_no, line_no
            r = pymupdf.Rect(w[:4])
            lines[key] = lines[key] | r if key in lines else r
        offset = sum(len(w[4]) + 1 for w in words[: span[0]])
        return Match(
            page_index=pi,
            page_label=page.get_label() or str(pi + 1),
            rects=list(lines.values()),
            text=" ".join(w[4] for w in hit),
            char_offset=offset,
        )
    return None


def zotero_position(doc: pymupdf.Document, m: Match) -> dict:
    """Convert a match to Zotero's annotationPosition (PDF user space, bottom-left origin)."""
    page = doc[m.page_index]
    to_pdf = ~page.transformation_matrix
    rects = []
    for r in m.rects:
        pr = (r * to_pdf).normalize()
        rects.append([round(pr.x0, 3), round(pr.y0, 3), round(pr.x1, 3), round(pr.y1, 3)])
    rects.sort(key=lambda r: (-r[3], r[0]))
    return {"pageIndex": m.page_index, "rects": rects}


def zotero_sort_index(m: Match) -> str:
    top = int(min(r.y0 for r in m.rects))
    return f"{m.page_index:05d}|{min(m.char_offset, 999999):06d}|{max(top, 0):05d}"


def write_preview(doc: pymupdf.Document, highlights: list[tuple[Match, str, str]], out: Path) -> None:
    """Save a copy of the PDF with the highlights embedded, for checking results without Zotero."""
    copy = pymupdf.open()
    copy.insert_pdf(doc)
    for m, color_hex, comment in highlights:
        page = copy[m.page_index]
        annot = page.add_highlight_annot(m.rects)
        rgb = tuple(int(color_hex[i : i + 2], 16) / 255 for i in (1, 3, 5))
        annot.set_colors(stroke=rgb)
        annot.set_info(content=comment, title="paper-annotator")
        annot.update()
    out.parent.mkdir(parents=True, exist_ok=True)
    copy.save(out)
