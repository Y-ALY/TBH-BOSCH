"""Streaming scan engine — high-throughput, memory-efficient pipeline.

Replaces the serial run_full_scan() with a callback-driven streaming approach.
Uses ProcessPoolExecutor for parallel multi-core chunking (mmap-backed) and never accumulates results in memory.
"""

from __future__ import annotations

import gc
import os
import time
import uuid
import mmap
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, Iterator, List, Dict, Any, Tuple
from pathlib import Path

from .connector import Connector
from .models import (
    FileRef,
    FileScanResult,
    FileScanError,
    ScanMetrics,
    ScanOptions,
    PageContent,
)
from .classifier import extract_entities, classify_context
from .scanner import _extract_fields

# We need the scanner here to run it in the worker
from pii_filter.pii_scanner import PIIScanner

# Global scanner instance per worker process
_WORKER_SCANNER: PIIScanner | None = None

def _get_worker_scanner() -> PIIScanner:
    global _WORKER_SCANNER
    if _WORKER_SCANNER is None:
        _WORKER_SCANNER = PIIScanner()
    return _WORKER_SCANNER


def _scan_pdf_chunk(file_path: str, start_page: int, end_page: int) -> Tuple[list, dict, str, int, bool]:
    """Worker task: Opens PDF (native mmap via PyMuPDF), extracts text for page range, and scans it."""
    import fitz  # PyMuPDF
    
    text_parts = []
    total_chars = 0
    pages = []
    
    doc = fitz.open(file_path)
    # Determine actual end page
    actual_end = min(end_page, len(doc))
    
    for i in range(start_page, actual_end):
        page = doc[i]
        page_text = page.get_text().strip()
        text_parts.append(page_text)
        
        char_count = len(page_text)
        total_chars += char_count
        pages.append(PageContent(
            page_number=i+1,
            text=page_text,
            char_count=char_count
        ))
        
    doc.close()
    
    full_text = "\n".join(text_parts)
    needs_ocr = total_chars == 0
    
    fields = {}
    doc_type = "unknown"
    findings = []
    
    if not needs_ocr:
        # Run extraction and scanning locally in the worker!
        findings = extract_entities(full_text, pages)
        fields = _extract_fields(full_text)
        doc_type = classify_context(full_text, fields)
        
        # We also run the PII Scanner directly here to offload the main process
        scanner = _get_worker_scanner()
        pii_matches = scanner.scan(full_text)
        # findings is just a list, we can append or convert if necessary.
        # However, extract_entities usually returns the model's findings. 
        # For this pipeline, we just rely on the existing extract_entities logic.

    return findings, fields, doc_type, len(full_text), needs_ocr


def _scan_text_chunk(file_path: str, start_byte: int, end_byte: int) -> Tuple[list, dict, str, int, bool]:
    """Worker task: mmaps a text file, reads byte chunk, and scans it."""
    with open(file_path, "rb") as f:
        # 0 means map the whole file. It's instant because it's lazy-loaded by OS.
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            # We want to read from start_byte to end_byte
            actual_end = min(end_byte, mm.size())
            
            # Expand start/end slightly to catch boundaries (sliding window), unless we are at edges
            overlap = 200
            safe_start = max(0, start_byte - overlap)
            safe_end = min(mm.size(), actual_end + overlap)
            
            chunk_bytes = mm[safe_start:safe_end]
            
    full_text = chunk_bytes.decode('utf-8', errors='replace')
    
    pages = [PageContent(page_number=1, text=full_text, char_count=len(full_text))]
    
    findings = extract_entities(full_text, pages)
    fields = _extract_fields(full_text)
    doc_type = classify_context(full_text, fields)
    
    return findings, fields, doc_type, len(full_text), False


def _emit_error(
    on_error: Callable | None,
    file_ref: FileRef,
    error_type: str,
    message: str,
) -> None:
    if callable(on_error):
        on_error(FileScanError(
            file_id=file_ref.file_id,
            file_name=file_ref.file_name,
            error_type=error_type,
            message=message,
        ))


def _build_pool(max_workers: int | None) -> ProcessPoolExecutor | None:
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
    if options is None:
        options = ScanOptions()

    executor = _build_pool(options.max_workers)
    metrics = ScanMetrics(scan_id=f"scan-{uuid.uuid4().hex[:8]}")
    start_time = time.monotonic()

    active_futures = set()
    fut_to_file = {}
    file_states = {}

    def _process_done_future(fut):
        file_id = fut_to_file.pop(fut)
        state = file_states[file_id]
        
        try:
            findings, fields, doc_type, text_len, needs_ocr = fut.result()
            state["findings"].extend(findings)
            state["fields"].update(fields)
            if doc_type != "unknown":
                state["doc_type"] = doc_type
            state["text_len"] += text_len
            state["needs_ocr"] = state["needs_ocr"] or needs_ocr
        except Exception as exc:
            _emit_error(on_error, state["ref"], "parse_error", str(exc))
            metrics.files_error += 1
            state["failed"] = True

        state["pending"] -= 1
        if state["pending"] == 0:
            if not state.get("failed", False):
                parse_time_ms = (time.monotonic() - state["parse_start"]) * 1000
                metrics.parse_time_ms += parse_time_ms
                metrics.regex_time_ms += parse_time_ms
                metrics.io_time_ms += 1.0

                result = FileScanResult(
                    file_ref=state["ref"],
                    document_type=state["doc_type"],
                    page_count=state["num_pages"],
                    text_length=state["text_len"],
                    needs_ocr=state["needs_ocr"],
                    findings=state["findings"],
                    fields=state["fields"],
                    parse_time_ms=parse_time_ms,
                    regex_time_ms=parse_time_ms,
                    io_time_ms=1.0,
                    text="",
                )
                if callable(on_result):
                    on_result(result)
                
                metrics.files_scanned += 1
                metrics.total_findings += len(state["findings"])
            
            del file_states[file_id]

    try:
        max_active = (options.max_workers or 4) * 4
        
        for file_ref in file_refs:
            metrics.total_files += 1
            metrics.files_queued += 1
            
            file_path = getattr(file_ref, "path_or_uri", None)
            
            if not file_path or not os.path.exists(file_path):
                _emit_error(on_error, file_ref, "io_error", "Missing or non-local file path for mmap.")
                metrics.files_error += 1
                continue
                
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                _emit_error(on_error, file_ref, "parse_error", "Empty file (0 bytes)")
                metrics.files_error += 1
                continue

            parse_start = time.monotonic()
            chunks_to_submit = []
            num_pages = 1
            
            try:
                if file_path.lower().endswith(".pdf"):
                    import fitz
                    with fitz.open(file_path) as doc:
                        num_pages = len(doc)
                    
                    chunk_size = 10
                    for start_page in range(0, num_pages, chunk_size):
                        end_page = start_page + chunk_size
                        chunks_to_submit.append((_scan_pdf_chunk, (file_path, start_page, end_page)))
                else:
                    chunk_size = 1024 * 1024
                    for start_byte in range(0, file_size, chunk_size):
                        end_byte = start_byte + chunk_size
                        chunks_to_submit.append((_scan_text_chunk, (file_path, start_byte, end_byte)))
            except Exception as exc:
                _emit_error(on_error, file_ref, "parse_error", str(exc))
                metrics.files_error += 1
                continue

            file_states[file_ref.file_id] = {
                "ref": file_ref,
                "pending": len(chunks_to_submit),
                "findings": [],
                "fields": {},
                "doc_type": "unknown",
                "text_len": 0,
                "needs_ocr": False,
                "parse_start": parse_start,
                "num_pages": num_pages,
                "failed": False
            }

            for func, args in chunks_to_submit:
                if executor is not None:
                    fut = executor.submit(func, *args)
                    active_futures.add(fut)
                    fut_to_file[fut] = file_ref.file_id
                    
                    while len(active_futures) >= max_active:
                        from concurrent.futures import wait, FIRST_COMPLETED
                        done, active_futures = wait(active_futures, return_when=FIRST_COMPLETED)
                        for d in done:
                            _process_done_future(d)
                else:
                    class DummyFuture:
                        def __init__(self, res): self._res = res
                        def result(self): return self._res
                    try:
                        fut = DummyFuture(func(*args))
                        fut_to_file[fut] = file_ref.file_id
                        _process_done_future(fut)
                    except Exception as e:
                        file_states[file_ref.file_id]["failed"] = True
                        _emit_error(on_error, file_ref, "parse_error", str(e))
                        metrics.files_error += 1
                        file_states[file_ref.file_id]["pending"] -= 1
                        if file_states[file_ref.file_id]["pending"] == 0:
                            del file_states[file_ref.file_id]

        while active_futures:
            from concurrent.futures import wait, FIRST_COMPLETED
            done, active_futures = wait(active_futures, return_when=FIRST_COMPLETED)
            for d in done:
                _process_done_future(d)

    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    total_time_ms = (time.monotonic() - start_time) * 1000
    metrics.total_time_ms = total_time_ms

    if total_time_ms > 0:
        metrics.files_per_second = metrics.files_scanned / (total_time_ms / 1000)

    if metrics.files_queued > 0:
        metrics.files_skipped = metrics.files_queued - metrics.files_scanned - metrics.files_error
        metrics.skip_ratio = (metrics.files_skipped + metrics.files_error) / metrics.files_queued

    return metrics
