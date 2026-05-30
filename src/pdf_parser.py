"""PDF text extraction — page-by-page using pdfplumber.

Single public function: parse_pdf(file_bytes) -> (pages, needs_ocr).
"""

from __future__ import annotations

import io

import pdfplumber

from .models import PageContent


def parse_pdf(file_bytes: bytes) -> tuple[list[PageContent], bool]:
    """Extract text from each page of a PDF.

    Returns:
        pages: list of PageContent, one per page.
        needs_ocr: True if no extractable text was found (image-based PDF).
    """
    pages: list[PageContent] = []
    total_chars = 0

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            char_count = len(text)
            total_chars += char_count
            pages.append(PageContent(
                page_number=i,
                text=text,
                char_count=char_count,
            ))

    needs_ocr = total_chars == 0
    return pages, needs_ocr
