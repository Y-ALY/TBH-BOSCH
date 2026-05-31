"""Delta planner — compare incoming FileRefs against saved state using metadata fingerprints.

DeltaPlanner.plan() streams through FileRefs and decides which to scan vs skip.
No content hashing unless ScanOptions.strict_hash is True.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .delta import compute_fingerprint
from .models import FileRef, FileSnapshot, FileScanResult, ScanOptions

logger = logging.getLogger(__name__)


@dataclass
class DeltaPlan:
    """Result of delta planning — which files to scan, which to skip."""
    scan_id: str
    to_scan: list[FileRef] = field(default_factory=list)
    to_skip: list[FileRef] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total_discovered: int = 0
    skip_ratio: float = 0.0

    def __post_init__(self):
        if not self.scan_id:
            self.scan_id = f"delta-{uuid.uuid4().hex[:8]}"


class DeltaPlanner:
    """Compares incoming files against saved state to produce a scan plan.

    Usage::

        planner = DeltaPlanner(state_dir="data/state")

        # Build a plan
        plan = planner.plan(discover_local("./repo"), ScanOptions())

        # After scanning, persist state for next delta
        planner.save_state(plan.scan_id, scan_results_iterator)
    """

    def __init__(self, state_dir: str = "data/state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    def plan(self, files: Iterator[FileRef], options: ScanOptions) -> DeltaPlan:
        """Compare incoming FileRefs against saved state, return a DeltaPlan.

        For each file:
        - New file (not in previous state) → queue for scan
        - Fingerprint matches previous state → skip
        - Fingerprint differs → queue for scan

        Files in previous state but absent from the incoming stream are reported
        as ``missing``.
        """
        plan = DeltaPlan(scan_id="")

        prev_state = self.load_previous_state()
        prev_files: dict[str, FileSnapshot] = prev_state if prev_state else {}
        prev_ids_seen: set[str] = set()

        for file_ref in files:
            plan.total_discovered += 1
            fp = compute_fingerprint(file_ref)

            prev_snap = prev_files.get(file_ref.file_id)
            if prev_snap is None:
                # New file — not in previous state
                plan.to_scan.append(file_ref)
            elif prev_snap.change_token == fp:
                # Fingerprint matches — skip
                plan.to_skip.append(file_ref)
                prev_ids_seen.add(file_ref.file_id)
            else:
                # Fingerprint differs — queue for scan
                plan.to_scan.append(file_ref)
                prev_ids_seen.add(file_ref.file_id)

        # Files in previous state but not in current stream → missing
        plan.missing = sorted(set(prev_files.keys()) - prev_ids_seen)

        # Calculate skip ratio
        if plan.total_discovered > 0:
            plan.skip_ratio = len(plan.to_skip) / plan.total_discovered

        logger.info(
            "Delta plan: %d total, %d to scan, %d skip, %d missing, ratio=%.2f%%",
            plan.total_discovered,
            len(plan.to_scan),
            len(plan.to_skip),
            len(plan.missing),
            plan.skip_ratio * 100,
        )
        return plan

    # ------------------------------------------------------------------
    # save_state
    # ------------------------------------------------------------------

    def save_state(self, scan_id: str, results: Iterator[FileScanResult]) -> str:
        """Save FileSnapshot per scanned file for next delta comparison.

        Writes both a timestamped state file and updates ``latest.json``.
        Returns the path to the timestamped state file.
        """
        files: dict[str, FileSnapshot] = {}
        for result in results:
            ref = result.file_ref
            fp = compute_fingerprint(ref)
            files[ref.file_id] = FileSnapshot(
                file_id=ref.file_id,
                content_hash="",            # not computed unless strict_hash
                last_modified=ref.last_modified,
                change_token=fp,
            )

        state_dict = {
            "scan_id": scan_id,
            "timestamp": datetime.now().isoformat(),
            "connector_type": "DeltaPlanner",
            "files": {
                fid: {
                    "file_id": fs.file_id,
                    "content_hash": fs.content_hash,
                    "last_modified": fs.last_modified,
                    "change_token": fs.change_token,
                }
                for fid, fs in files.items()
            },
        }

        # Timestamped state file
        state_path = self.state_dir / f"delta_state_{scan_id}.json"
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2, ensure_ascii=False)

        # Update latest.json
        latest_path = self.state_dir / "latest.json"
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2, ensure_ascii=False)

        logger.info("State saved: %s (%d files)", state_path, len(files))
        return str(state_path)

    # ------------------------------------------------------------------
    # load_previous_state
    # ------------------------------------------------------------------

    def load_previous_state(self) -> dict[str, FileSnapshot] | None:
        """Load the most recent state file (``latest.json``).

        Returns a dict keyed by file_id, or None if no previous state exists.
        """
        latest_path = self.state_dir / "latest.json"
        if not latest_path.exists():
            logger.info("No previous state found at %s", latest_path)
            return None

        with open(latest_path) as f:
            data = json.load(f)

        files: dict[str, FileSnapshot] = {}
        for fid, fdata in data.get("files", {}).items():
            files[fid] = FileSnapshot(
                file_id=fdata.get("file_id", fid),
                content_hash=fdata.get("content_hash", ""),
                last_modified=fdata.get("last_modified", ""),
                change_token=fdata.get("change_token", ""),
            )
        return files
