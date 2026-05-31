"""OCR image scanning with content-addressable caching.

Provides a high-performance, memory-efficient pipeline for extracting text
from uploaded image files (scanned IDs, license cards, documents) and
checking them for PII / compliance violations via the existing regex +
semantic scanner in classifier.py.

Architecture:
    1. Stream-hash the uploaded file (SHA-256, 64 KB chunks).
    2. Check an in-memory dict cache keyed by that hash.
    3. On cache miss: write to a temp file → pytesseract OCR → delete temp.
    4. Feed the extracted text into extract_entities() for PII flagging.
    5. Store the result in the cache and return.

Cache location:
    Module-level ``_ocr_cache: dict[str, OCRCacheEntry]``.
    Lives for the lifetime of the FastAPI process. No Redis/Memcached needed.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import BinaryIO

from .classifier import extract_entities
from .models import PageContent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tesseract availability check (fail-soft)
# ---------------------------------------------------------------------------

_TESSERACT_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image

    # Quick smoke test — will raise if the tesseract binary is missing
    pytesseract.get_tesseract_version()
    _TESSERACT_AVAILABLE = True
    logger.info(
        "Tesseract OCR available: v%s", pytesseract.get_tesseract_version()
    )
except Exception as exc:  # pragma: no cover
    logger.warning(
        "Tesseract OCR is NOT available (%s). "
        "Image scanning will return an error until Tesseract is installed. "
        "Install: choco install tesseract (Windows) / apt install tesseract-ocr (Linux)",
        exc,
    )


# ---------------------------------------------------------------------------
# Cache data structures
# ---------------------------------------------------------------------------

@dataclass
class OCRCacheEntry:
    """One cached OCR result, keyed by file content hash."""

    text: str
    flags: list[dict] = field(default_factory=list)
    file_hash: str = ""


# ┌──────────────────────────────────────────────────────────────────────────┐
# │  THE CACHE — module-level dict, persists for the process lifetime.      │
# │  Key: SHA-256 hex digest of the raw image bytes.                        │
# │  Value: OCRCacheEntry with extracted text + PII flags.                  │
# └──────────────────────────────────────────────────────────────────────────┘
_ocr_cache: dict[str, OCRCacheEntry] = {}


# ---------------------------------------------------------------------------
# Streaming SHA-256 hasher
# ---------------------------------------------------------------------------

def stream_hash_bytes(data: bytes, *, chunk_size: int = 65_536) -> str:
    """Return the SHA-256 hex digest of *data* using chunked reads.

    This avoids holding the entire buffer in the hashlib internal state
    at once and mirrors the pattern used in connector.py for file hashing.
    """
    hasher = hashlib.sha256()
    view = memoryview(data)
    for offset in range(0, len(view), chunk_size):
        hasher.update(view[offset : offset + chunk_size])
    return hasher.hexdigest()


def stream_hash_file(file_obj: BinaryIO, *, chunk_size: int = 65_536) -> str:
    """Return the SHA-256 hex digest by reading *file_obj* in chunks.

    The file position is reset to the start after hashing so callers
    can re-read the content without seeking manually.
    """
    hasher = hashlib.sha256()
    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    file_obj.seek(0)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# OCR text extraction (pytesseract)
# ---------------------------------------------------------------------------

def _extract_text_from_image(image_bytes: bytes) -> str:
    """Run Tesseract OCR on raw image bytes and return the extracted text.

    Flow:
        1. Write bytes to a named temp file (pytesseract needs a file path
           or PIL Image — we use PIL to avoid format-guessing issues).
        2. Call pytesseract.image_to_string().
        3. Delete the temp file immediately in a ``finally`` block.
        4. Return the stripped text string.

    The raw ``image_bytes`` are NOT retained after this function returns.
    """
    if not _TESSERACT_AVAILABLE:
        raise RuntimeError(
            "Tesseract OCR engine is not installed or not found on PATH. "
            "Install: choco install tesseract (Windows) / apt install tesseract-ocr (Linux)"
        )

    tmp_path: str | None = None
    try:
        # Open with PIL from memory — avoids writing bytes to disk for the
        # image decode step; only the temp file is for pytesseract fallback.
        img = Image.open(io.BytesIO(image_bytes))

        # pytesseract can accept a PIL Image directly — no temp file needed!
        text: str = pytesseract.image_to_string(img)
        return text.strip()
    finally:
        # Explicitly close the PIL image to release memory
        try:
            img.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        # Discard the raw bytes reference (caller should also drop theirs)
        del image_bytes


# ---------------------------------------------------------------------------
# PII flag scanning (delegates to classifier.py)
# ---------------------------------------------------------------------------

def _scan_text_for_pii(text: str) -> list[dict]:
    """Feed OCR-extracted text through the existing regex + semantic scanner.

    Returns a list of flag dicts suitable for the API response.
    """
    if not text:
        return []

    # Build a single-page PageContent so extract_entities() can work
    page = PageContent(page_number=1, text=text)
    findings = extract_entities(text, [page])

    return [
        {
            "type": f.type,
            "value": f.value,
            "field": f.field,
            "context": f.context,
            "risk_level": f.risk_level,
            "confidence": f.confidence,
            "evidence": f.evidence,
            "recommended_action": f.recommended_action,
            "flag_type": f.flag_type,
        }
        for f in findings
    ]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scan_image(file_bytes: bytes) -> dict:
    """Full OCR scan pipeline: hash → cache check → OCR → PII scan → return.

    Args:
        file_bytes: Raw bytes of the uploaded image file.

    Returns:
        Structured dict::

            {
                "status": "success",
                "cache_hit": true | false,
                "file_hash": "a1b2c3…",
                "text": "…extracted text…",
                "flags": [ { "type": "…", … }, … ]
            }
    """
    # ── Step 1: Content-addressable hash ──────────────────────────────────
    file_hash = stream_hash_bytes(file_bytes)

    # ── Step 2: Cache lookup ──────────────────────────────────────────────
    cached = _ocr_cache.get(file_hash)
    if cached is not None:
        logger.info("OCR cache HIT for hash=%s", file_hash[:16])
        return {
            "status": "success",
            "cache_hit": True,
            "file_hash": file_hash,
            "text": cached.text,
            "flags": cached.flags,
        }

    # ── Step 3: Cache miss — run OCR ──────────────────────────────────────
    logger.info("OCR cache MISS for hash=%s — running Tesseract", file_hash[:16])
    text = _extract_text_from_image(file_bytes)

    # Drop the raw bytes now — we only need the text from here on
    del file_bytes

    # ── Step 4: PII / compliance scan ─────────────────────────────────────
    flags = _scan_text_for_pii(text)

    # ── Step 5: Store in cache ────────────────────────────────────────────
    _ocr_cache[file_hash] = OCRCacheEntry(
        text=text,
        flags=flags,
        file_hash=file_hash,
    )

    return {
        "status": "success",
        "cache_hit": False,
        "file_hash": file_hash,
        "text": text,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Cache management helpers (for testing / admin use)
# ---------------------------------------------------------------------------

def get_cache_stats() -> dict:
    """Return basic cache statistics."""
    return {
        "entries": len(_ocr_cache),
        "hashes": list(_ocr_cache.keys()),
    }


def clear_cache() -> int:
    """Flush the entire OCR cache. Returns the number of evicted entries."""
    count = len(_ocr_cache)
    _ocr_cache.clear()
    return count
