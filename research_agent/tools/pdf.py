"""Extract bounded text PDF windows in memory, retaining page numbers and gaps."""

import io
import re

from pypdf import PdfReader

from research_agent.config import ResearchError


def read_pdf(body, budget, *, max_pages=120):
    """Read text pages under the existing deadline; do not OCR or execute PDF content."""
    if not body.startswith(b"%PDF-"):
        raise ResearchError("pdf_invalid", "The response is not a PDF document.")
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
        if reader.is_encrypted:
            raise ResearchError("pdf_encrypted", "Encrypted PDF text is unavailable.")
        pages = []
        total = len(reader.pages)
        for index, page in enumerate(reader.pages):
            budget.check()
            if index >= max_pages:
                break
            text = page.extract_text() or ""
            text = re.sub(
                r"(?<=[\u4e00-\u9fff])[ \t\u3000]+(?=[\u4e00-\u9fff])", "", text
            )
            pages.append(
                "\n".join(
                    " ".join(line.split()) for line in text.splitlines() if line.strip()
                )[:50_000]
            )
        if not any(page.strip() for page in pages):
            raise ResearchError(
                "pdf_no_text", "No text layer was found; OCR is unavailable."
            )
        return pages, total
    except ResearchError:
        raise
    except Exception:
        raise ResearchError("pdf_invalid", "PDF text extraction failed.") from None


def pdf_window(pages, total, *, focus="", max_chars=24_000, start_page=0):
    """Return an actual contiguous page window, warning when the focus was not found."""
    if not isinstance(focus, str) or len(focus) > 200:
        raise ResearchError("invalid_input", "Use a short literal PDF reading focus.")
    if (
        isinstance(start_page, bool)
        or not isinstance(start_page, int)
        or not 0 <= start_page <= len(pages)
    ):
        raise ResearchError(
            "invalid_input", "PDF page number is outside the readable pages."
        )
    needle = re.sub(r"\s+", "", focus).casefold()
    section = re.match(r"^§?\s*(\d+(?:\.\d+)*)(?:\s|[^\d.]|$)", focus)
    literal, headings, exact_headings, numbered = [], [], [], []
    for index, text in enumerate(pages):
        if needle and needle in re.sub(r"\s+", "", text).casefold():
            literal.append(index)
        for line in text.splitlines():
            compact = re.sub(r"\s+", "", line).casefold()
            if re.search(r"[.．]{4}|…{2}", compact):
                continue
            if (
                needle
                and needle in compact
                and re.match(r"^(?:§?\d+(?:\.\d+)*)", compact)
            ):
                headings.append(index)
                if re.sub(r"^§?\d+(?:\.\d+)*", "", compact).strip("、.：:") == needle:
                    exact_headings.append(index)
            if section and re.match(
                rf"^§?{re.escape(section.group(1))}(?:[^\d.]|$)", compact
            ):
                numbered.append(index)
    matches = numbered or exact_headings or headings or literal
    body_matches = [
        i
        for i in matches
        if not re.search(r"\.{4}|…{2}|1\.2\s*目\s*录|重要提示及目录", pages[i])
    ]
    found = next(iter(body_matches or matches), None)
    start = start_page - 1 if start_page else found if found is not None else 0
    lines, selected, count = [], [], 0
    for index in range(start, len(pages)):
        text = pages[index]
        remaining = max_chars - count
        if remaining <= 0:
            break
        excerpt = text[:remaining]
        lines.append(f"[PDF page {index + 1}] {excerpt}")
        selected.append(index + 1)
        count += len(excerpt)
    return {
        "text": "\n".join(lines),
        "pages": selected,
        "page_count": total,
        "truncated": start > 0 or len(selected) < total or count >= max_chars,
        **({"focus_matched": found is not None} if focus else {}),
        **(
            {
                "focus_match_mode": "section"
                if numbered
                else "heading"
                if headings
                else "literal"
            }
            if found is not None
            else {}
        ),
        **(
            {"reading_warning": "Focus not found; returned the first text pages."}
            if focus and found is None
            else {}
        ),
    }
