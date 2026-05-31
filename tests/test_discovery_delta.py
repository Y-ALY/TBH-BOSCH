"""Tests for discovery and delta planning (Agent 1).

Covers:
- discover_local() finds PDFs and sets etag correctly
- DeltaPlanner first run queues all files
- DeltaPlanner second run skips unchanged files
- Fingerprint changes on size / etag differences
- Missing file detection
- Streaming doesn't blow memory
- skip_ratio calculation
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.discovery import discover_local
from src.delta_planner import DeltaPlanner, DeltaPlan
from src.delta import compute_fingerprint
from src.models import FileRef, FileScanResult, ScanOptions


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _demo_repo() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "strict_drive")


def _make_fileref(
    file_id: str = "local:test.pdf",
    size_bytes: int = 100,
    last_modified: str = "2024-01-01T00:00:00",
    etag_or_version: str = "abc",
) -> FileRef:
    return FileRef(
        file_id=file_id,
        file_name=file_id.split(":", 1)[-1],
        path_or_uri=f"/tmp/{file_id}",
        source_type="local",
        size_bytes=size_bytes,
        last_modified=last_modified,
        etag_or_version=etag_or_version,
    )


# ---------------------------------------------------------------------------
# discover_local
# ---------------------------------------------------------------------------

def test_discover_local_finds_pdfs():
    """discover_local() should find PDF files in strict_drive."""
    files = list(discover_local(_demo_repo()))
    assert len(files) > 1000, f"Expected >1000 PDFs, got {len(files)}"
    for f in files[:5]:
        assert isinstance(f, FileRef)
        assert f.source_type == "local"
        assert f.file_name.endswith(".pdf")


def test_discover_local_etag_format():
    """discover_local() should set etag_or_version to '{mtime}_{size}'."""
    for f in discover_local(_demo_repo()):
        assert f.etag_or_version, "etag should not be empty"
        parts = f.etag_or_version.rsplit("_", 1)
        assert len(parts) == 2, f"Expected 'mtime_size' format, got {f.etag_or_version}"
        assert parts[1].isdigit(), f"Size part should be numeric, got {parts[1]}"
        break  # Check first file only


def test_discover_local_file_ref_has_required_fields():
    """Each FileRef from discover_local should have all required fields."""
    for f in discover_local(_demo_repo()):
        assert f.file_id, "file_id should not be empty"
        assert f.file_name, "file_name should not be empty"
        assert f.path_or_uri, "path_or_uri should not be empty"
        assert f.size_bytes > 0, "size_bytes should be positive"
        assert f.last_modified, "last_modified should not be empty"
        assert f.etag_or_version, "etag_or_version should not be empty"
        break


def test_discover_local_nonexistent_path():
    """discover_local() should return empty iterator for nonexistent path."""
    files = list(discover_local("/nonexistent/path/xyz"))
    assert len(files) == 0


# ---------------------------------------------------------------------------
# DeltaPlanner — first run
# ---------------------------------------------------------------------------

def test_delta_planner_first_run_all_to_scan():
    """First run with no saved state: all files go to to_scan."""
    files = list(discover_local(_demo_repo()))

    with tempfile.TemporaryDirectory() as tmpdir:
        planner = DeltaPlanner(state_dir=tmpdir)
        plan = planner.plan(iter(files), ScanOptions())

        assert len(plan.to_scan) == len(files), (
            f"First run: all {len(files)} should be to_scan, got {len(plan.to_scan)}"
        )
        assert len(plan.to_skip) == 0
        assert plan.total_discovered == len(files)
        assert plan.skip_ratio == 0.0


# ---------------------------------------------------------------------------
# DeltaPlanner — second run (delta)
# ---------------------------------------------------------------------------

def test_delta_planner_second_run_skips_unchanged():
    """Second run with saved state: unchanged files should skip."""
    files = list(discover_local(_demo_repo()))

    with tempfile.TemporaryDirectory() as tmpdir:
        planner = DeltaPlanner(state_dir=tmpdir)

        # First run — save state (simulate)
        def make_results():
            for fr in files:
                yield FileScanResult(file_ref=fr)

        planner.save_state("scan-test-001", make_results())

        # Second run — should skip all unchanged files
        plan = planner.plan(iter(files), ScanOptions())

        assert len(plan.to_scan) == 0, (
            f"Second run: expected 0 to_scan, got {len(plan.to_scan)}"
        )
        assert len(plan.to_skip) == len(files), (
            f"Second run: all {len(files)} should skip, got {len(plan.to_skip)} skipped"
        )
        assert plan.skip_ratio == 1.0


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_changes_when_size_changes():
    """Fingerprint should change when size_bytes differs."""
    fr1 = _make_fileref(size_bytes=100)
    fr2 = _make_fileref(size_bytes=200)
    assert compute_fingerprint(fr1) != compute_fingerprint(fr2)


def test_fingerprint_changes_when_etag_changes():
    """Fingerprint should change when etag_or_version differs."""
    fr1 = _make_fileref(etag_or_version="v1")
    fr2 = _make_fileref(etag_or_version="v2")
    assert compute_fingerprint(fr1) != compute_fingerprint(fr2)


def test_fingerprint_changes_when_last_modified_changes():
    """Fingerprint should change when last_modified differs."""
    fr1 = _make_fileref(last_modified="2024-01-01T00:00:00")
    fr2 = _make_fileref(last_modified="2024-06-15T12:00:00")
    assert compute_fingerprint(fr1) != compute_fingerprint(fr2)


def test_fingerprint_changes_when_source_type_changes():
    """Fingerprint should change when source_type differs."""
    fr1 = FileRef(
        file_id="test", file_name="t.pdf", path_or_uri="/t.pdf",
        source_type="local", size_bytes=100, last_modified="t1",
        etag_or_version="e1",
    )
    fr2 = FileRef(
        file_id="test", file_name="t.pdf", path_or_uri="/t.pdf",
        source_type="onedrive", size_bytes=100, last_modified="t1",
        etag_or_version="e1",
    )
    assert compute_fingerprint(fr1) != compute_fingerprint(fr2)


def test_fingerprint_stable_for_same_metadata():
    """Same metadata should produce the same fingerprint."""
    fr1 = _make_fileref()
    fr2 = _make_fileref()
    assert compute_fingerprint(fr1) == compute_fingerprint(fr2)


# ---------------------------------------------------------------------------
# Missing detection
# ---------------------------------------------------------------------------

def test_missing_detection():
    """Files in state but not on disk should appear in plan.missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_dir = os.path.join(tmpdir, "state")
        os.makedirs(state_dir, exist_ok=True)

        # Create a state with a ghost file and a present file
        state = {
            "scan_id": "scan-test",
            "timestamp": "2024-01-01T00:00:00",
            "connector_type": "test",
            "files": {
                "local:ghost.pdf": {
                    "file_id": "local:ghost.pdf",
                    "content_hash": "",
                    "last_modified": "2024-01-01T00:00:00",
                    "change_token": "local:local:ghost.pdf:500:2024-01-01T00:00:00:old",
                },
                "local:present.pdf": {
                    "file_id": "local:present.pdf",
                    "content_hash": "",
                    "last_modified": "2024-01-01T00:00:00",
                    "change_token": "local:local:present.pdf:100:2024-01-01T00:00:00:tok",
                },
            },
        }
        with open(os.path.join(state_dir, "latest.json"), "w") as f:
            json.dump(state, f)

        planner = DeltaPlanner(state_dir=state_dir)

        # Stream contains only present.pdf
        present = _make_fileref(
            file_id="local:present.pdf",
            size_bytes=100,
            last_modified="2024-01-01T00:00:00",
            etag_or_version="tok",
        )
        plan = planner.plan(iter([present]), ScanOptions())

        assert plan.missing == ["local:ghost.pdf"]
        assert len(plan.to_scan) == 0  # present.pdf unchanged -> skip
        assert len(plan.to_skip) == 1


# ---------------------------------------------------------------------------
# Streaming / memory
# ---------------------------------------------------------------------------

def test_streaming_large_directory_no_memory_blow():
    """Processing a large directory via streaming should not blow memory."""
    count = 0
    for _f in discover_local(_demo_repo()):
        count += 1
        # Process and forget — never accumulate in a list
    assert count > 1000, f"Expected >1000 files, got {count}"
    # If we got here without MemoryError, streaming works


# ---------------------------------------------------------------------------
# skip_ratio
# ---------------------------------------------------------------------------

def test_skip_ratio_calculation():
    """skip_ratio should be to_skip / total_discovered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        planner = DeltaPlanner(state_dir=tmpdir)

        ref_a = _make_fileref(file_id="local:a.pdf", size_bytes=1,
                              etag_or_version="e1")
        ref_b = _make_fileref(file_id="local:b.pdf", size_bytes=2,
                              etag_or_version="e2")
        ref_c = _make_fileref(file_id="local:c.pdf", size_bytes=3,
                              etag_or_version="e3")

        # Save state for file 'a' only
        def results_a():
            yield FileScanResult(file_ref=ref_a)

        planner.save_state("s1", results_a())

        # Second run: a unchanged (skip), b and c are new (scan)
        plan = planner.plan(iter([ref_a, ref_b, ref_c]), ScanOptions())

        assert plan.total_discovered == 3
        assert len(plan.to_skip) == 1   # a
        assert len(plan.to_scan) == 2   # b, c
        assert plan.skip_ratio == 1.0 / 3.0


# ---------------------------------------------------------------------------
# DeltaPlan defaults
# ---------------------------------------------------------------------------

def test_delta_plan_defaults():
    """DeltaPlan should have sensible defaults and auto-generated scan_id."""
    plan = DeltaPlan(scan_id="")
    assert plan.scan_id.startswith("delta-")
    assert plan.to_scan == []
    assert plan.to_skip == []
    assert plan.missing == []
    assert plan.total_discovered == 0
    assert plan.skip_ratio == 0.0


# ---------------------------------------------------------------------------
# load_previous_state
# ---------------------------------------------------------------------------

def test_load_previous_state_returns_none_when_no_file():
    """load_previous_state() should return None when latest.json doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        planner = DeltaPlanner(state_dir=tmpdir)
        result = planner.load_previous_state()
        assert result is None


# ---------------------------------------------------------------------------
# save_state writes files
# ---------------------------------------------------------------------------

def test_save_state_writes_latest_and_timestamped_files():
    """save_state() should create both latest.json and a timestamped file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        planner = DeltaPlanner(state_dir=tmpdir)

        ref = _make_fileref()
        results = [FileScanResult(file_ref=ref)]

        path = planner.save_state("scan-abc123", iter(results))

        # Timestamped file exists
        assert os.path.exists(path)
        assert path.endswith("delta_state_scan-abc123.json")

        # latest.json exists
        latest_path = os.path.join(tmpdir, "latest.json")
        assert os.path.exists(latest_path)

        # latest.json has the right content
        with open(latest_path) as f:
            data = json.load(f)
        assert data["scan_id"] == "scan-abc123"
        assert "local:test.pdf" in data["files"]
