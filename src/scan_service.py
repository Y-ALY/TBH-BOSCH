"""Scan service — single high-level entry point for all scan modes.

Delegates to the appropriate existing function in src/scanner.py or
src/streaming_scanner.py. Does NOT reimplement any scan logic.

Usage:
    from src.scan_service import scan_folder

    result = scan_folder(folder_path="./demo_drive_rich", mode="full")
    # result -> {"scan_id": ..., "files_scanned": ..., "findings_count": ...}

    result = scan_folder(
        folder_path="./demo_drive_rich",
        mode="streaming",
        on_result_callback=handle_file_result,
    )
"""

from __future__ import annotations

import logging
from typing import Callable

from .connector import Connector, LocalSampleRepoConnector
from .models import ScanResult, ScanMetrics, FileScanResult

logger = logging.getLogger(__name__)

# Valid modes and their descriptions for documentation
_VALID_MODES = ("full", "ai", "layered", "streaming")
_VALID_AI_MODES = ("off", "layered", "full")


def scan_folder(
    folder_path: str,
    mode: str = "full",
    ai_mode: str = "off",
    db_session=None,
    on_result_callback: Callable[[FileScanResult], None] | None = None,
    owner_hints_file: str | None = None,
) -> dict:
    """Run a scan on a folder, delegating to the appropriate scan engine.

    This is the RECOMMENDED entry point for triggering scans from API
    handlers, CLIs, or background jobs. It wraps the underlying scan
    functions with a consistent interface.

    Args:
        folder_path: Path to the directory containing documents to scan.
        mode: Scan mode — one of:
            "full"      — Batch regex-only scan (run_full_scan).
            "ai"        — Batch scan + AI enrichment (run_ai_scan).
            "layered"   — Streaming regex + async AI via AIQueue
                          (run_layered_scan). Returns metrics.
            "streaming"  — High-throughput streaming scan
                          (run_streaming_scan). Returns metrics.
        ai_mode: AI behavior — "off" (no AI), "layered", or "full".
            Only meaningful for "layered" and "streaming" modes.
        db_session: Optional SQLAlchemy session for Employee lookups
            during owner assignment.
        on_result_callback: For streaming/layered modes, called with
            each FileScanResult as it completes. Ignored for batch modes.
        owner_hints_file: Optional path to owner hints JSON. Passed to
            LocalSampleRepoConnector.

    Returns:
        dict with keys:
            scan_id (str), files_scanned (int), findings_count (int),
            ai_enabled (bool), mode (str).
        For streaming/layered modes, also includes metrics from
        ScanMetrics (total_time_ms, files_per_second, etc.).
    """
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid mode '{mode}'. Must be one of: {_VALID_MODES}"
        )
    if ai_mode not in _VALID_AI_MODES:
        raise ValueError(
            f"Invalid ai_mode '{ai_mode}'. Must be one of: {_VALID_AI_MODES}"
        )

    connector = LocalSampleRepoConnector(
        repo_path=folder_path,
        owner_hints_file=owner_hints_file,
    )

    if mode == "full":
        return _run_full(connector, db_session)
    elif mode == "ai":
        return _run_ai(connector, ai_mode, db_session)
    elif mode == "layered":
        return _run_layered(connector, ai_mode, db_session, on_result_callback)
    elif mode == "streaming":
        return _run_streaming(connector, ai_mode, db_session, on_result_callback)
    else:
        raise ValueError(f"Unhandled mode: {mode}")


# ------------------------------------------------------------------
# Internal dispatchers
# ------------------------------------------------------------------


def _run_full(connector: Connector, db_session) -> dict:
    """Batch regex-only scan."""
    from .scanner import run_full_scan as _full

    result: ScanResult = _full(connector, db_session=db_session)

    return {
        "scan_id": result.scan_id,
        "files_scanned": result.files_scanned,
        "findings_count": len(result.findings),
        "ai_enabled": False,
        "mode": "full",
        "raw": result,
    }


def _run_ai(connector: Connector, ai_mode: str, db_session) -> dict:
    """Batch scan with AI enrichment."""
    from .scanner import run_ai_scan as _ai

    # Resolve AI parser
    ai_parser = None
    if ai_mode != "off":
        try:
            from .ai_parser import AIParser

            ai_parser = AIParser()
        except Exception:
            logger.warning("AI parser unavailable, falling back to regex-only.")

    result: ScanResult = _ai(connector, ai_parser=ai_parser, db_session=db_session)

    return {
        "scan_id": result.scan_id,
        "files_scanned": result.files_scanned,
        "findings_count": len(result.findings),
        "ai_enabled": ai_parser is not None,
        "mode": "ai",
        "raw": result,
    }


def _run_layered(
    connector: Connector,
    ai_mode: str,
    db_session,
    on_result_callback,
) -> dict:
    """Streaming regex + async AI enrichment."""
    from .models import ScanOptions
    from .scanner import run_layered_scan as _layered

    ai_parser = None
    if ai_mode != "off":
        try:
            from .ai_parser import AIParser

            ai_parser = AIParser()
        except Exception:
            logger.warning("AI parser unavailable for layered scan.")

    file_refs = list(connector.iter_files())

    metrics, ai_queue = _layered(
        connector,
        file_refs,
        options=ScanOptions(mode="delta", ai_mode=ai_mode),
        ai_parser=ai_parser,
        db_session=db_session,
        on_result=on_result_callback,
    )

    return {
        "scan_id": metrics.scan_id,
        "files_scanned": metrics.files_scanned,
        "findings_count": metrics.total_findings,
        "ai_enabled": ai_parser is not None,
        "mode": "layered",
        "metrics": {
            "total_time_ms": metrics.total_time_ms,
            "files_per_second": metrics.files_per_second,
            "files_error": metrics.files_error,
            "skip_ratio": metrics.skip_ratio,
        },
    }


def _run_streaming(
    connector: Connector,
    ai_mode: str,
    db_session,
    on_result_callback,
) -> dict:
    """High-throughput streaming scan (no AI enrichment)."""
    from .models import ScanOptions
    from .streaming_scanner import run_streaming_scan as _streaming

    file_refs = list(connector.iter_files())

    metrics: ScanMetrics = _streaming(
        connector,
        iter(file_refs),
        options=ScanOptions(mode="full", ai_mode="off"),
        db_session=db_session,
        on_result=on_result_callback,
    )

    return {
        "scan_id": metrics.scan_id,
        "files_scanned": metrics.files_scanned,
        "findings_count": metrics.total_findings,
        "ai_enabled": False,
        "mode": "streaming",
        "metrics": {
            "total_time_ms": metrics.total_time_ms,
            "files_per_second": metrics.files_per_second,
            "files_error": metrics.files_error,
            "skip_ratio": metrics.skip_ratio,
        },
    }
