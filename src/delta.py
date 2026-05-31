"""Delta scan — compare current source state against a previous baseline.

- save_state(): persist a ScanResult as a DeltaState JSON snapshot.
- compare_delta(): diff current connector state against a saved state.
- compute_fingerprint(): fast metadata-based fingerprint (no I/O).
- compute_content_hash(): SHA-256 hash of file contents (for strict_hash mode).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .connector import Connector
from .models import (
    ScanResult,
    DeltaState,
    DeltaReport,
    FileMetadata,
    FileRef,
    FileSnapshot,
)


def save_state(scan_result: ScanResult, state_dir: str) -> str:
    """Persist scan state to JSON for future delta comparisons.

    Also writes/overwrites `latest.json` as a convenience.
    """
    out_dir = Path(state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, FileSnapshot] = {}
    for doc in scan_result.parsed_documents:
        files[doc.file_id] = FileSnapshot(
            file_id=doc.file_id,
            content_hash=doc.content_hash,
            last_modified="",  # connector doesn't expose per-file mtime in ScanResult
            change_token=scan_result.change_token,
        )

    state = DeltaState(
        scan_id=scan_result.scan_id,
        timestamp=scan_result.timestamp,
        connector_type=scan_result.connector_type,
        files=files,
    )

    # Write timestamped state file
    state_path = out_dir / f"delta_state_{state.scan_id}.json"
    _write_json(state_path, _delta_state_to_dict(state))

    # Also write latest.json
    latest_path = out_dir / "latest.json"
    _write_json(latest_path, _delta_state_to_dict(state))

    return str(state_path)


def compare_delta(
    connector: Connector,
    previous_state_path: str,
) -> DeltaReport:
    """Compare current connector state against a previously saved DeltaState.

    Returns a DeltaReport showing added, modified, removed, and unchanged files.
    """
    # Load previous state
    prev_path = Path(previous_state_path)
    if not prev_path.exists():
        raise FileNotFoundError(f"Previous state file not found: {previous_state_path}")

    with open(prev_path) as f:
        prev_data = json.load(f)

    prev_files: dict[str, dict] = prev_data.get("files", {})
    prev_scan_id = prev_data.get("scan_id", "unknown")

    # Get current state
    current_files = connector.list_files()
    current_map: dict[str, FileMetadata] = {f.file_id: f for f in current_files}
    current_ids = set(current_map.keys())
    prev_ids = set(prev_files.keys())

    # Build report
    report = DeltaReport(
        scan_id="",
        previous_scan_id=prev_scan_id,
        timestamp="",
    )

    # Added: in current but not in previous
    added_ids = current_ids - prev_ids
    report.added = [current_map[fid] for fid in sorted(added_ids)]

    # Removed: in previous but not in current
    report.removed = sorted(prev_ids - current_ids)
    report.missing = list(report.removed)  # alias per spec

    # Compare hashes for files in both sets
    for fid in sorted(current_ids & prev_ids):
        prev_hash = prev_files[fid].get("content_hash", "")
        curr_hash = current_map[fid].content_hash
        if prev_hash != curr_hash:
            report.modified.append(current_map[fid])
        else:
            report.unchanged += 1

    return report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _delta_state_to_dict(state: DeltaState) -> dict:
    return {
        "scan_id": state.scan_id,
        "timestamp": state.timestamp,
        "connector_type": state.connector_type,
        "files": {
            fid: {
                "file_id": fs.file_id,
                "content_hash": fs.content_hash,
                "last_modified": fs.last_modified,
                "change_token": fs.change_token,
            }
            for fid, fs in state.files.items()
        },
    }


# ---------------------------------------------------------------------------
# Fingerprint helpers (Agent 1)
# ---------------------------------------------------------------------------

def compute_fingerprint(file_ref: FileRef) -> str:
    """Fast metadata-based fingerprint — no I/O, no content hash.

    Uses source_type + file_id + size + last_modified + etag_or_version.
    Changing any of these will produce a different fingerprint.
    """
    return (
        f"{file_ref.source_type}:{file_ref.file_id}:"
        f"{file_ref.size_bytes}:{file_ref.last_modified}:"
        f"{file_ref.etag_or_version}"
    )


def compute_content_hash(file_ref: FileRef, connector: Connector) -> str:
    """SHA-256 hash of file contents. Only used when strict_hash=True.

    Opens the file via connector.open_file() and reads in chunks to
    avoid loading the entire file into memory.
    """
    hasher = hashlib.sha256()
    fh = connector.open_file(file_ref)
    try:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    finally:
        fh.close()
    return hasher.hexdigest()
