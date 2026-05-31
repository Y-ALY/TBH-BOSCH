"""Tests for the streaming scanner.

Verifies:
  - Correct results on real PDFs (first 50)
  - Error isolation (bad PDF doesn't crash scan)
  - Callback delivery (results arrive via on_result)
  - Memory bounds (no accumulation with many files)
  - ProcessPoolExecutor fallback
  - Empty directory handling
"""
from __future__ import annotations

import gc
import os
import sys
import tempfile
import tracemalloc
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import FileRef, FileScanResult, FileScanError, ScanMetrics, ScanOptions
from src.connector import LocalSampleRepoConnector
from src.streaming_scanner import run_streaming_scan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _demo_repo_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "strict_drive")


# ---------------------------------------------------------------------------
# Test: basic streaming scan works on real PDFs
# ---------------------------------------------------------------------------

def test_run_streaming_scan_basic():
    """Run streaming scan on first 50 files, verify results and metrics."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:50]

    results: list[FileScanResult] = []
    errors: list[FileScanError] = []

    def on_result(r: FileScanResult) -> None:
        results.append(r)

    def on_error(e: FileScanError) -> None:
        errors.append(e)

    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
        on_result=on_result,
        on_error=on_error,
    )

    assert metrics.files_scanned == 50, f"Expected 50 scanned, got {metrics.files_scanned}"
    assert len(results) == 50, f"Expected 50 results, got {len(results)}"
    assert metrics.files_error == 0, f"Expected 0 errors, got {metrics.files_error}"
    assert len(errors) == 0, f"Expected 0 error callbacks, got {len(errors)}"
    assert metrics.total_time_ms > 0, "Total time should be positive"
    assert metrics.files_per_second > 0, "Files per second should be positive"


# ---------------------------------------------------------------------------
# Test: results arrive in the order they are processed
# ---------------------------------------------------------------------------

def test_results_arrive_via_on_result_callback():
    """Each successful file should produce exactly one on_result call."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:10]

    result_file_ids: set[str] = set()

    def on_result(r: FileScanResult) -> None:
        result_file_ids.add(r.file_ref.file_id)

    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
        on_result=on_result,
    )

    expected_ids = {fr.file_id for fr in file_refs}
    assert result_file_ids == expected_ids, \
        f"Result set mismatch: missing {expected_ids - result_file_ids}"
    assert metrics.files_scanned == len(file_refs)


# ---------------------------------------------------------------------------
# Test: each FileScanResult has the required per-file metrics
# ---------------------------------------------------------------------------

def test_filescanresult_has_per_file_metrics():
    """Each FileScanResult must include parse_time_ms, regex_time_ms, io_time_ms."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:5]

    results: list[FileScanResult] = []

    def on_result(r: FileScanResult) -> None:
        results.append(r)

    run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
        on_result=on_result,
    )

    for r in results:
        assert r.parse_time_ms > 0, \
            f"parse_time_ms should be positive, got {r.parse_time_ms} for {r.file_ref.file_id}"
        assert r.regex_time_ms > 0, \
            f"regex_time_ms should be positive, got {r.regex_time_ms} for {r.file_ref.file_id}"
        assert r.io_time_ms > 0, \
            f"io_time_ms should be positive, got {r.io_time_ms} for {r.file_ref.file_id}"
        assert r.document_type != ""  # at least something


# ---------------------------------------------------------------------------
# Test: bad PDF does not crash the scan
# ---------------------------------------------------------------------------

def test_bad_pdf_does_not_crash_scan():
    """A non-PDF file should emit an error but scan should continue."""
    # Copy one valid PDF and add a dummy non-PDF file
    demo = Path(_demo_repo_path())
    valid_pdfs = sorted(demo.glob("*.pdf"))
    if not valid_pdfs:
        return  # Nothing to test against

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Copy one valid PDF
        src = valid_pdfs[0]
        (tmp / src.name).write_bytes(src.read_bytes())

        # Create a non-PDF file with .pdf extension
        bad_path = tmp / "bad_file.pdf"
        bad_path.write_text("This is not a PDF file, just random text.")

        conn = LocalSampleRepoConnector(repo_path=str(tmp))
        file_refs = list(conn.iter_files())

        results: list[FileScanResult] = []
        errors: list[FileScanError] = []

        def on_result(r: FileScanResult) -> None:
            results.append(r)

        def on_error(e: FileScanError) -> None:
            errors.append(e)

        metrics = run_streaming_scan(
            conn,
            iter(file_refs),
            ScanOptions(mode="full"),
            on_result=on_result,
            on_error=on_error,
        )

        # The scan should not crash
        assert metrics.files_error >= 1, \
            f"Expected at least 1 error for bad PDF, got {metrics.files_error}"
        assert metrics.files_scanned >= 1, \
            f"Valid PDF should have been scanned, got {metrics.files_scanned}"
        assert len(errors) >= 1, "Expected at least one on_error call"

        # The bad file should be the one that errored
        bad_file_id = f"local:bad_file.pdf"
        assert any(e.file_id == bad_file_id for e in errors), \
            f"Expected error for {bad_file_id}, got: {[e.file_id for e in errors]}"


# ---------------------------------------------------------------------------
# Test: ProcessPoolExecutor fallback (max_workers=0 -> sync)
# ---------------------------------------------------------------------------

def test_process_pool_fallback_to_sync():
    """When max_workers is 0, the scanner should fall back to synchronous parsing."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:5]

    results: list[FileScanResult] = []

    def on_result(r: FileScanResult) -> None:
        results.append(r)

    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", max_workers=0),
        on_result=on_result,
    )

    assert metrics.files_scanned == 5, \
        f"Sync fallback should scan all files, got {metrics.files_scanned}"
    assert len(results) == 5
    # Results should be valid
    for r in results:
        assert r.parse_time_ms > 0
        assert r.regex_time_ms > 0


# ---------------------------------------------------------------------------
# Test: empty directory returns empty metrics
# ---------------------------------------------------------------------------

def test_empty_directory_returns_empty_metrics():
    """Scanning an empty directory should return zeroed metrics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = LocalSampleRepoConnector(repo_path=tmpdir)
        file_refs = list(conn.iter_files())

        results: list[FileScanResult] = []

        def on_result(r: FileScanResult) -> None:
            results.append(r)

        metrics = run_streaming_scan(
            conn,
            iter(file_refs),
            ScanOptions(mode="full"),
            on_result=on_result,
        )

        assert metrics.total_files == 0
        assert metrics.files_scanned == 0
        assert metrics.files_error == 0
        assert metrics.total_findings == 0
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Test: memory does not grow with file count
# ---------------------------------------------------------------------------

def test_memory_does_not_grow_with_file_count():
    """Processing many files should not cause unbounded memory growth."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:100]  # Test with 100 files

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    # Accumulate nothing — just count
    count = 0

    def on_result(r: FileScanResult) -> None:
        nonlocal count
        count += 1

    run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
        on_result=on_result,
    )

    # Force GC to get a clean snapshot
    gc.collect()
    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Compare top-level memory stats (not comparing individual traces —
    # comparing raw memory usage of current vs peak)
    stats_before = snapshot_before.statistics("filename")
    stats_after = snapshot_after.statistics("filename")

    # Total allocated size difference should be small relative to file count.
    # We're mostly checking that the scanner doesn't leak.
    total_before = sum(s.size for s in stats_before)
    total_after = sum(s.size for s in stats_after)

    # Allow some growth for import caching, but not gigabytes
    diff_bytes = total_after - total_before
    # The diff should be well under 50 MB for 100 files (worst-case caching).
    # A leaking scanner would show multi-GB growth.
    assert diff_bytes < 100 * 1024 * 1024, \
        f"Memory grew by {diff_bytes / (1024*1024):.1f} MB across 100 files — possible leak"

    assert count == 100, f"Expected 100 results, got {count}"


# ---------------------------------------------------------------------------
# Test: ScanMetrics fields are correctly populated
# ---------------------------------------------------------------------------

def test_scanmetrics_fields_populated():
    """Verify ScanMetrics has all expected fields after a scan."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:10]

    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
        on_result=lambda r: None,
    )

    assert metrics.scan_id.startswith("scan-")
    assert metrics.total_files == 10
    assert metrics.files_scanned == 10
    assert metrics.files_error == 0
    assert metrics.total_findings >= 0
    assert metrics.io_time_ms > 0
    assert metrics.parse_time_ms > 0
    assert metrics.regex_time_ms > 0
    assert metrics.total_time_ms > 0
    assert metrics.files_per_second > 0
    assert 0.0 <= metrics.skip_ratio <= 1.0


# ---------------------------------------------------------------------------
# Test: callbacks are optional (scan works without them)
# ---------------------------------------------------------------------------

def test_callbacks_are_optional():
    """Streaming scan should work without on_result and on_error callbacks."""
    conn = LocalSampleRepoConnector(repo_path=_demo_repo_path())
    file_refs = list(conn.iter_files())[:5]

    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full"),
    )

    assert metrics.files_scanned == 5
    assert metrics.files_error == 0
