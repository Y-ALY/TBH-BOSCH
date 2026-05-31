"""Streaming file discovery — lightweight FileRef iteration, no content hashing.

- discover_files(): wraps a connector's iter_files() with progress logging.
- discover_local(): direct filesystem walk, no connector needed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .connector import Connector
from .models import FileRef

logger = logging.getLogger(__name__)


def discover_files(connector: Connector) -> Iterator[FileRef]:
    """Wrap connector.iter_files() with progress logging and count tracking.

    Streams FileRef objects one at a time — never accumulates in memory.
    """
    count = 0
    for file_ref in connector.iter_files():
        count += 1
        if count % 1000 == 0:
            logger.info("Discovered %d files via connector...", count)
        yield file_ref
    logger.info("Discovery complete: %d files found", count)


def discover_local(path: str, recursive: bool = True) -> Iterator[FileRef]:
    """Walk a local directory and yield FileRef objects for PDF files.

    Does NOT require a Connector. Handles permission errors gracefully.
    etag_or_version is set to "{mtime:.0f}_{size}" for fingerprint-based delta.
    """
    base = Path(path).resolve()
    if not base.exists():
        logger.warning("Path does not exist: %s", base)
        return

    count = 0
    if recursive:
        walker = base.rglob("*.pdf")
    else:
        walker = base.glob("*.pdf")

    for pdf_path in walker:
        try:
            stat = pdf_path.stat()
        except OSError as e:
            logger.warning("Skipping unreadable file %s: %s", pdf_path, e)
            continue

        count += 1
        if count % 1000 == 0:
            logger.info("Discovered %d local files...", count)

        mtime_ts = stat.st_mtime
        etag = f"{mtime_ts:.0f}_{stat.st_size}"
        yield FileRef(
            file_id=f"local:{str(pdf_path.relative_to(base))}",
            file_name=pdf_path.name,
            path_or_uri=str(pdf_path),
            source_type="local",
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(mtime_ts).isoformat(),
            etag_or_version=etag,
        )

    logger.info("Local discovery complete: %d files found", count)
