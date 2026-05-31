"""Unit tests for Agent 0 shared contracts.

Verifies that all new models and connector methods work correctly,
and that backward compatibility is preserved.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import (
    FileRef,
    ScanOptions,
    FileScanResult,
    FileScanError,
    ScanMetrics,
    ScanJob,
    FileMetadata,
    ScanResult,
    Finding,
    ALLOWED_REVIEW_ACTIONS,
)
from src.connector import Connector, LocalSampleRepoConnector


# ---------------------------------------------------------------------------
# FileRef
# ---------------------------------------------------------------------------

def test_fileref_default_mime_type():
    """FileRef should default mime_type to 'application/pdf'."""
    fr = FileRef(
        file_id="test-1",
        file_name="doc.pdf",
        path_or_uri="/tmp/doc.pdf",
        source_type="local",
        size_bytes=1024,
        last_modified="2024-01-01T00:00:00",
        etag_or_version="abc123",
    )
    assert fr.mime_type == "application/pdf"
    assert fr.file_id == "test-1"
    assert fr.source_type == "local"


def test_fileref_custom_mime_type():
    """FileRef should accept a custom mime_type."""
    fr = FileRef(
        file_id="test-2",
        file_name="data.csv",
        path_or_uri="/data/data.csv",
        source_type="onedrive",
        size_bytes=512,
        last_modified="2024-06-15T12:00:00",
        etag_or_version="xyz789",
        mime_type="text/csv",
    )
    assert fr.mime_type == "text/csv"


# ---------------------------------------------------------------------------
# ScanOptions
# ---------------------------------------------------------------------------

def test_scanoptions_defaults():
    """ScanOptions should default to delta mode, layered AI, no strict_hash."""
    so = ScanOptions()
    assert so.mode == "delta"
    assert so.ai_mode == "layered"
    assert so.strict_hash is False
    assert so.max_workers is None


def test_scanoptions_custom():
    """ScanOptions should accept custom values."""
    so = ScanOptions(mode="full", strict_hash=True, ai_mode="full", max_workers=4)
    assert so.mode == "full"
    assert so.strict_hash is True
    assert so.ai_mode == "full"
    assert so.max_workers == 4


# ---------------------------------------------------------------------------
# FileScanResult
# ---------------------------------------------------------------------------

def _make_fileref(file_id="f1") -> FileRef:
    return FileRef(
        file_id=file_id,
        file_name="test.pdf",
        path_or_uri="/tmp/test.pdf",
        source_type="local",
        size_bytes=100,
        last_modified="2024-01-01T00:00:00",
        etag_or_version="abc",
    )


def test_filescanresult_is_error_when_error_set():
    """is_error should be True when error is set."""
    fr = _make_fileref()
    fsr = FileScanResult(file_ref=fr, error="parse failed")
    assert fsr.is_error is True


def test_filescanresult_is_error_when_no_error():
    """is_error should be False when error is None."""
    fr = _make_fileref()
    fsr = FileScanResult(file_ref=fr)
    assert fsr.is_error is False


def test_filescanresult_defaults():
    """FileScanResult should have sensible defaults."""
    fr = _make_fileref()
    fsr = FileScanResult(file_ref=fr)
    assert fsr.document_type == "unknown"
    assert fsr.page_count == 0
    assert fsr.text_length == 0
    assert fsr.needs_ocr is False
    assert fsr.findings == []
    assert fsr.fields == {}
    assert fsr.owner_hints == {}
    assert fsr.parse_time_ms == 0.0
    assert fsr.regex_time_ms == 0.0
    assert fsr.error is None


# ---------------------------------------------------------------------------
# FileScanError
# ---------------------------------------------------------------------------

def test_filescanerror_fields():
    """FileScanError should store error metadata."""
    fe = FileScanError(
        file_id="local:bad.pdf",
        file_name="bad.pdf",
        error_type="parse_error",
        message="Corrupted PDF header",
    )
    assert fe.file_id == "local:bad.pdf"
    assert fe.file_name == "bad.pdf"
    assert fe.error_type == "parse_error"
    assert fe.message == "Corrupted PDF header"


# ---------------------------------------------------------------------------
# ScanMetrics
# ---------------------------------------------------------------------------

def test_scanmetrics_defaults():
    """ScanMetrics should default all counters to zero."""
    sm = ScanMetrics()
    assert sm.scan_id == ""
    assert sm.total_files == 0
    assert sm.files_queued == 0
    assert sm.files_skipped == 0
    assert sm.files_scanned == 0
    assert sm.files_error == 0
    assert sm.total_findings == 0
    assert sm.discovery_time_ms == 0.0
    assert sm.delta_time_ms == 0.0
    assert sm.io_time_ms == 0.0
    assert sm.parse_time_ms == 0.0
    assert sm.regex_time_ms == 0.0
    assert sm.db_write_time_ms == 0.0
    assert sm.ai_time_ms == 0.0
    assert sm.total_time_ms == 0.0
    assert sm.peak_memory_mb == 0.0
    assert sm.files_per_second == 0.0
    assert sm.mb_per_second == 0.0
    assert sm.skip_ratio == 0.0


# ---------------------------------------------------------------------------
# ScanJob
# ---------------------------------------------------------------------------

def test_scanjob_default_status_is_pending():
    """ScanJob should default status to 'pending'."""
    sj = ScanJob(scan_id="scan-123")
    assert sj.status == "pending"
    assert sj.scan_id == "scan-123"
    assert sj.options == {}
    assert sj.created_at == ""
    assert sj.started_at is None
    assert sj.completed_at is None
    assert sj.metrics is None
    assert sj.error_count == 0


def test_scanjob_custom_status():
    """ScanJob should accept a custom status."""
    sj = ScanJob(scan_id="scan-456", status="running")
    assert sj.status == "running"


# ---------------------------------------------------------------------------
# LocalSampleRepoConnector — iter_files
# ---------------------------------------------------------------------------

def test_iter_files_yields_fileref_objects():
    """iter_files() should yield FileRef instances."""
    repo = os.path.join(os.path.dirname(__file__), "..", "demo_drive_rich")
    conn = LocalSampleRepoConnector(repo_path=repo)
    files = list(conn.iter_files())
    assert len(files) > 0, "Expected at least one PDF in demo_drive_rich"
    for f in files:
        assert isinstance(f, FileRef), f"Expected FileRef, got {type(f)}"
    assert all(f.source_type == "local" for f in files)


def test_iter_files_file_ref_has_required_fields():
    """Each FileRef from iter_files should have all required fields populated."""
    repo = os.path.join(os.path.dirname(__file__), "..", "demo_drive_rich")
    conn = LocalSampleRepoConnector(repo_path=repo)
    for f in conn.iter_files():
        assert f.file_id, "file_id should not be empty"
        assert f.file_name, "file_name should not be empty"
        assert f.path_or_uri, "path_or_uri should not be empty"
        assert f.size_bytes > 0, "size_bytes should be positive"
        assert f.last_modified, "last_modified should not be empty"
        assert f.etag_or_version, "etag_or_version should not be empty"
        break  # Just check the first file


# ---------------------------------------------------------------------------
# LocalSampleRepoConnector — open_file
# ---------------------------------------------------------------------------

def test_open_file_returns_readable_stream():
    """open_file() should return a readable binary file object."""
    repo = os.path.join(os.path.dirname(__file__), "..", "demo_drive_rich")
    conn = LocalSampleRepoConnector(repo_path=repo)
    # Get first file ref
    first = next(conn.iter_files())
    assert first is not None
    fh = conn.open_file(first)
    try:
        data = fh.read(100)  # Read first 100 bytes
        assert len(data) > 0, "Should read data from the file"
        assert isinstance(data, bytes)
    finally:
        fh.close()


def test_open_file_on_nonexistent_file_raises():
    """open_file() should raise FileNotFoundError for a bogus file_id."""
    conn = LocalSampleRepoConnector(repo_path="/tmp")
    bogus = FileRef(
        file_id="local:nonexistent.pdf",
        file_name="nonexistent.pdf",
        path_or_uri="/tmp/nonexistent.pdf",
        source_type="local",
        size_bytes=0,
        last_modified="2024-01-01T00:00:00",
        etag_or_version="none",
    )
    try:
        conn.open_file(bogus)
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_filemetadata_still_works():
    """FileMetadata should still be importable and constructable."""
    fm = FileMetadata(
        file_id="local:test.pdf",
        file_name="test.pdf",
        path="/tmp/test.pdf",
        size_bytes=500,
        last_modified="2024-01-01T00:00:00",
        content_hash="abc123def456",
    )
    assert fm.file_id == "local:test.pdf"
    assert fm.content_hash == "abc123def456"
    assert fm.mime_type == "application/pdf"


def test_scanresult_still_works():
    """ScanResult should still be constructable."""
    sr = ScanResult(
        scan_id="scan-test",
        timestamp="2024-01-01T00:00:00",
        connector_type="LocalSampleRepoConnector",
    )
    assert sr.scan_id == "scan-test"
    assert sr.parsed_documents == []
    assert sr.findings == []


def test_finding_still_works():
    """Finding should still be constructable."""
    f = Finding(
        finding_id="",
        file_id="local:test.pdf",
        type="email",
        value="user@example.com",
    )
    assert f.type == "email"
    assert f.finding_id.startswith("finding-")


def test_list_files_still_returns_filemetadata():
    """list_files() should still return FileMetadata objects (backward compat)."""
    repo = os.path.join(os.path.dirname(__file__), "..", "demo_drive_rich")
    conn = LocalSampleRepoConnector(repo_path=repo)
    files = conn.list_files()
    assert len(files) > 0
    for fm in files:
        assert isinstance(fm, FileMetadata), f"Expected FileMetadata, got {type(fm)}"


def test_allowed_review_actions_still_defined():
    """ALLOWED_REVIEW_ACTIONS should still be importable and non-empty."""
    assert len(ALLOWED_REVIEW_ACTIONS) > 0
    assert "retain" in ALLOWED_REVIEW_ACTIONS
    assert "delete" in ALLOWED_REVIEW_ACTIONS


# ---------------------------------------------------------------------------
# Connector ABC — abstract methods
# ---------------------------------------------------------------------------

def test_connector_abc_requires_iter_files():
    """Subclassing Connector without iter_files should raise TypeError."""
    try:
        class BadConnector(Connector):
            def list_files(self): return []
            def get_file_metadata(self, fid): return None
            def download_file(self, fid): return b""
            def open_file(self, fr): raise NotImplementedError
            def get_owner_hints(self, fid): return {}
            def get_change_token(self): return ""

        BadConnector()
        assert False, "Expected TypeError for missing iter_files"
    except TypeError:
        pass


def test_connector_abc_requires_open_file():
    """Subclassing Connector without open_file should raise TypeError."""
    try:
        class BadConnector(Connector):
            def list_files(self): return []
            def iter_files(self): return iter([])
            def get_file_metadata(self, fid): return None
            def download_file(self, fid): return b""
            def get_owner_hints(self, fid): return {}
            def get_change_token(self): return ""

        BadConnector()
        assert False, "Expected TypeError for missing open_file"
    except TypeError:
        pass
