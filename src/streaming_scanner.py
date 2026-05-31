"""Streaming scan engine — high-throughput, memory-efficient pipeline.

Replaces the serial run_full_scan() with a callback-driven streaming approach.
Uses ProcessPoolExecutor for PDF parsing and never accumulates results in memory.
"""

from __future__ import annotations

import gc
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Iterator

from .connector import Connector
from .models import (
    FileRef,
    FileScanResult,
    FileScanError,
    ScanMetrics,
    ScanOptions,
)
from .pdf_parser import parse_pdf
from .classifier import extract_entities, classify_context
from .scanner import _extract_fields


def _parse_in_worker(file_bytes: bytes):
    """Worker entry point for ProcessPoolExecutor.

    Must be a module-level function so the pool can pickle it.
    Simply delegates to parse_pdf.
    """
    return parse_pdf(file_bytes)


def _emit_error(
    on_error: Callable | None,
    file_ref: FileRef,
    error_type: str,
    message: str,
) -> None:
    """Emit a FileScanError via the on_error callback if one is provided."""
    if callable(on_error):
        on_error(FileScanError(
            file_id=file_ref.file_id,
            file_name=file_ref.file_name,
            error_type=error_type,
            message=message,
        ))


def _build_pool(max_workers: int | None) -> ProcessPoolExecutor | None:
    """Try to create a ProcessPoolExecutor. Returns None on failure."""
    if max_workers is not None and max_workers <= 0:
        return None
    if max_workers is None:
        max_workers = min(os.cpu_count() or 1, 8)
    try:
        return ProcessPoolExecutor(max_workers=max_workers)
    except Exception:
        return None


def run_streaming_scan(
    connector: Connector,
    file_refs: Iterator[FileRef],
    options: ScanOptions | None = None,
    *,
    db_session=None,
    on_result: Callable[[FileScanResult], None] | None = None,
    on_error: Callable[[FileScanError], None] | None = None,
) -> ScanMetrics:
    """Streaming scan — processes files one at a time, emits results via callbacks.

    NEVER accumulates parsed_documents or findings in memory.
    Each file result is emitted immediately via on_result.

    Pipeline per file:
      1. connector.open_file(file_ref) — stream file bytes (IO)
      2. Parse PDF via ProcessPoolExecutor (CPU-heavy)
      3. Extract entities via regex (CPU-light)
      4. Classify document type
      5. Build FileScanResult with per-file metrics
      6. Call on_result(file_scan_result) immediately
      7. Release all per-file memory

    Args:
        connector:   Data source connector.
        file_refs:   Iterator of FileRef objects to scan.
        options:     Scan configuration (mode, ai_mode, max_workers, etc.).
        db_session:  Optional SQLAlchemy session (placeholder).
        on_result:   Called with each FileScanResult as it completes.
        on_error:    Called with each FileScanError for non-fatal failures.
    """
    if options is None:
        options = ScanOptions()

    executor = _build_pool(options.max_workers)
    metrics = ScanMetrics(scan_id=f"scan-{uuid.uuid4().hex[:8]}")
    start_time = time.monotonic()

    try:
        for file_ref in file_refs:
            metrics.total_files += 1
            metrics.files_queued += 1

            try:
                # ── 1. I/O: open file and read bytes ──
                io_start = time.monotonic()
                try:
                    with connector.open_file(file_ref) as fh:
                        raw_bytes = fh.read()
                except Exception as exc:
                    _emit_error(on_error, file_ref, "download_error", str(exc))
                    metrics.files_error += 1
                    continue
                io_time_ms = (time.monotonic() - io_start) * 1000
                metrics.io_time_ms += io_time_ms

                # Edge case: empty file
                if not raw_bytes:
                    _emit_error(on_error, file_ref, "parse_error", "Empty file (0 bytes)")
                    metrics.files_error += 1
                    gc.collect()
                    continue

                # ── 2. PDF parsing via ProcessPoolExecutor ──
                parse_start = time.monotonic()
                try:
                    if executor is not None:
                        future = executor.submit(_parse_in_worker, raw_bytes)
                        pages, needs_ocr = future.result(timeout=30)
                    else:
                        pages, needs_ocr = parse_pdf(raw_bytes)
                except FutureTimeoutError:
                    _emit_error(on_error, file_ref, "timeout", "PDF parsing timed out after 30s")
                    metrics.files_error += 1
                    gc.collect()
                    continue
                except Exception as exc:
                    _emit_error(on_error, file_ref, "parse_error", str(exc))
                    metrics.files_error += 1
                    gc.collect()
                    continue
                parse_time_ms = (time.monotonic() - parse_start) * 1000
                metrics.parse_time_ms += parse_time_ms

                # Release raw bytes as soon as parsing is done
                del raw_bytes

                # ── 3 & 4. Regex entity extraction + document classification ──
                regex_start = time.monotonic()
                full_text = "\n".join(p.text for p in pages)

                fields: dict = {}
                doc_type = "unknown"
                findings: list = []

                if not needs_ocr:
                    findings = extract_entities(full_text, pages)
                    fields = _extract_fields(full_text)
                    doc_type = classify_context(full_text, fields)

                regex_time_ms = (time.monotonic() - regex_start) * 1000
                metrics.regex_time_ms += regex_time_ms

                # ── 5. Build per-file result ──
                result = FileScanResult(
                    file_ref=file_ref,
                    document_type=doc_type,
                    page_count=len(pages),
                    text_length=len(full_text),
                    needs_ocr=needs_ocr,
                    findings=findings,
                    fields=fields,
                    parse_time_ms=parse_time_ms,
                    regex_time_ms=regex_time_ms,
                    io_time_ms=io_time_ms,
                )

                # ── 6. Emit result immediately ──
                if callable(on_result):
                    on_result(result)

                metrics.files_scanned += 1
                metrics.total_findings += len(findings)

                # ── 7. Release per-file memory ──
                del full_text
                del pages
                del fields
                del findings
                del result

            except Exception as exc:
                # Catch-all: any unexpected error must not terminate the scan
                _emit_error(on_error, file_ref, "parse_error", str(exc))
                metrics.files_error += 1

            # Force GC between files to prevent memory accumulation
            gc.collect()

    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # ── Finalize metrics ──
    total_time_ms = (time.monotonic() - start_time) * 1000
    metrics.total_time_ms = total_time_ms

    if total_time_ms > 0:
        metrics.files_per_second = metrics.files_scanned / (total_time_ms / 1000)

    if metrics.files_queued > 0:
        metrics.files_skipped = metrics.files_queued - metrics.files_scanned - metrics.files_error
        metrics.skip_ratio = (metrics.files_skipped + metrics.files_error) / metrics.files_queued

    return metrics
