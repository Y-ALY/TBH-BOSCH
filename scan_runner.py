"""Defer importing api/scanner until a background scan is actually started."""

from __future__ import annotations


def run_background_scan(
    scan_id: str,
    folder_path: str,
    mode: str,
    ai_mode: str,
    strict_hash: bool,
    source_type: str = "local",
    connection_config: dict | None = None,
) -> None:
    from api import _run_background_scan

    return _run_background_scan(
        scan_id,
        folder_path,
        mode,
        ai_mode,
        strict_hash,
        source_type=source_type,
        connection_config=connection_config,
    )
