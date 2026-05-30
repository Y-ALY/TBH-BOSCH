"""
file_ingestor.py – File Ingestion Layer for the Fast Filtering Pipeline.

Reads actual files from the filesystem, extracts plain text, and yields
:class:`DocumentInput` Pydantic objects that can be fed directly into
:meth:`FastFilterPipeline.process` or :meth:`FastFilterPipeline.process_batch`.

Supported formats:
    • ``.txt``  – plain-text (read via built-in ``open()``)
    • ``.pdf``  – PDF documents (extracted via PyMuPDF / ``fitz``)
    • ``.docx`` – Word documents (extracted via ``python-docx``)

Usage::

    from pii_filter.file_ingestor import ingest_directory
    from pii_filter import FastFilterPipeline

    pipeline = FastFilterPipeline()
    for flagged in pipeline.process(ingest_directory("./documents")):
        print(flagged.summary())
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Set

from pii_filter.models import DocumentInput

logger = logging.getLogger(__name__)

# ── Supported extensions ────────────────────────────────────
SUPPORTED_EXTENSIONS: Set[str] = {".txt", ".pdf", ".docx"}


# ── Text extraction helpers ─────────────────────────────────

def _extract_txt(file_path: Path) -> str:
    """Read plain-text content from a ``.txt`` file."""
    return file_path.read_text(encoding="utf-8", errors="replace")


def _extract_pdf(file_path: Path) -> str:
    """
    Extract text from a PDF using PyMuPDF (``fitz``).

    Concatenates text from all pages, separated by newlines.
    """
    import fitz  # PyMuPDF — lazy import to avoid hard dep if unused

    text_parts: list[str] = []
    with fitz.open(str(file_path)) as pdf_doc:
        for page in pdf_doc:
            text_parts.append(page.get_text())
    return "\n".join(text_parts)


def _extract_docx(file_path: Path) -> str:
    """
    Extract text from a ``.docx`` file using ``python-docx``.

    Extracts paragraph text (body content). Tables and headers/footers
    are intentionally skipped for the hackathon MVP — easy to extend.
    """
    from docx import Document as DocxDocument  # lazy import

    doc = DocxDocument(str(file_path))
    return "\n".join(paragraph.text for paragraph in doc.paragraphs)


# Dispatcher: extension → extractor function
_EXTRACTORS = {
    ".txt": _extract_txt,
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
}


# ── Metadata helpers ────────────────────────────────────────

def _stable_document_id(file_path: Path) -> str:
    """
    Generate a deterministic, stable document ID from the file's
    absolute path using SHA-256.

    Using the resolved absolute path means the same file always
    gets the same ID regardless of the working directory.
    """
    abs_path = str(file_path.resolve())
    return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()


def _get_last_modified(file_path: Path) -> datetime:
    """
    Read the OS-level last-modified timestamp and return it as a
    timezone-aware ``datetime`` (UTC).
    """
    mtime = file_path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


# ── Single-file ingestion ───────────────────────────────────

def ingest_file(file_path: Path | str) -> Optional[DocumentInput]:
    """
    Read a single file and return a :class:`DocumentInput`, or ``None``
    if the file is unsupported or cannot be read.

    Args:
        file_path: Path to the file (string or :class:`Path`).

    Returns:
        A populated ``DocumentInput`` or ``None`` on failure.
    """
    file_path = Path(file_path)

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.debug("Skipping unsupported file: %s", file_path.name)
        return None

    extractor = _EXTRACTORS.get(file_path.suffix.lower())
    if extractor is None:
        return None

    try:
        content = extractor(file_path)
    except Exception as exc:
        logger.warning(
            "⚠️  Failed to extract text from %s: %s — skipping.",
            file_path.name,
            exc,
        )
        return None

    if not content or not content.strip():
        logger.debug("Empty content after extraction: %s — skipping.", file_path.name)
        return None

    return DocumentInput(
        document_id=_stable_document_id(file_path),
        file_name=file_path.name,
        last_modified=_get_last_modified(file_path),
        content=content,
    )


# ── Directory ingestion ─────────────────────────────────────

def ingest_directory(folder_path: str | Path) -> Iterator[DocumentInput]:
    """
    Recursively scan *folder_path* for supported files and yield
    :class:`DocumentInput` objects.

    • Unsupported file extensions are silently ignored.
    • Corrupted / encrypted files log a warning and are skipped —
      one bad file never crashes the entire pipeline.

    Args:
        folder_path: Path to the root directory to scan.

    Yields:
        :class:`DocumentInput` for every successfully ingested file.

    Raises:
        FileNotFoundError: If *folder_path* does not exist.
        NotADirectoryError: If *folder_path* is not a directory.

    Example::

        from pii_filter.file_ingestor import ingest_directory
        from pii_filter import FastFilterPipeline

        pipeline = FastFilterPipeline()
        flagged = pipeline.process_batch(ingest_directory("./documents"))
    """
    root = Path(folder_path)

    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    logger.info("📂 Scanning directory: %s", root.resolve())

    ingested_count = 0
    skipped_count = 0

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped_count += 1
            continue

        doc = ingest_file(file_path)
        if doc is not None:
            ingested_count += 1
            logger.debug(
                "✅ Ingested [%d]: %s (%d chars)",
                ingested_count,
                doc.file_name,
                len(doc.content),
            )
            yield doc
        else:
            skipped_count += 1

    logger.info(
        "📊 Ingestion complete — ingested: %d | skipped/failed: %d",
        ingested_count,
        skipped_count,
    )
