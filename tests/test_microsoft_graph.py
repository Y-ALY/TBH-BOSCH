"""Tests for Microsoft Graph connectors — OneDrive and SharePoint.

All tests run offline using mock mode (client_id=\"mock\").
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.microsoft_graph import (
    MicrosoftGraphConnector,
    OneDriveConnector,
    SharePointConnector,
    _MockRetrySignal,
)
from src.models import FileRef, FileMetadata


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_onedrive_conn():
    """Create a OneDriveConnector in mock mode."""
    return OneDriveConnector(
        tenant_id="mock-tenant",
        client_id="mock",
        client_secret="mock-secret",
        user_id="mock-user@contoso.com",
    )


def _make_sharepoint_conn():
    """Create a SharePointConnector in mock mode."""
    return SharePointConnector(
        tenant_id="mock-tenant",
        client_id="mock",
        client_secret="mock-secret",
        site_id="mock-site",
        drive_id="mock-drive",
    )


# ---------------------------------------------------------------------------
# Paging tests
# ---------------------------------------------------------------------------

def test_iter_files_paging_one_drive():
    """OneDrive iter_files yields exactly 150 FileRef objects (3 pages x 50)."""
    conn = _make_onedrive_conn()
    files = list(conn.iter_files())
    assert len(files) == 150, f"Expected 150 files, got {len(files)}"
    for f in files:
        assert isinstance(f, FileRef), f"Expected FileRef, got {type(f)}"


def test_iter_files_paging_sharepoint():
    """SharePoint iter_files yields exactly 150 FileRef objects (3 pages x 50)."""
    conn = _make_sharepoint_conn()
    files = list(conn.iter_files())
    assert len(files) == 150, f"Expected 150 files, got {len(files)}"
    for f in files:
        assert isinstance(f, FileRef), f"Expected FileRef, got {type(f)}"


def test_paging_next_link_consumed():
    """Verify the mock consumed all pages by checking the page counter."""
    conn = _make_onedrive_conn()
    list(conn.iter_files())
    # After consuming 3 pages, the mock page counter should be at 3
    assert conn._mock_page == 3


# ---------------------------------------------------------------------------
# source_type tests
# ---------------------------------------------------------------------------

def test_onedrive_source_type():
    """OneDriveConnector FileRef should have source_type='onedrive'."""
    conn = _make_onedrive_conn()
    for f in conn.iter_files():
        assert f.source_type == "onedrive", f"Expected onedrive, got {f.source_type}"
        break  # Check first only


def test_sharepoint_source_type():
    """SharePointConnector FileRef should have source_type='sharepoint'."""
    conn = _make_sharepoint_conn()
    for f in conn.iter_files():
        assert f.source_type == "sharepoint", f"Expected sharepoint, got {f.source_type}"
        break


# ---------------------------------------------------------------------------
# 429 retry test
# ---------------------------------------------------------------------------

def test_429_retry_succeeds():
    """When the first Graph call returns 429, iter_files should retry and succeed.

    The mock's _mock_should_429 flag is True initially, causing the first
    _graph_request to raise _MockRetrySignal. The retry loop waits and
    re-invokes, at which point _mock_should_429 is False and data flows.
    """
    conn = _make_onedrive_conn()
    # Verify the mock is primed to 429
    assert conn._mock_should_429 is True
    # This should succeed despite the initial 429
    files = list(conn.iter_files())
    assert len(files) == 150
    # Flag should now be False because the 429 was consumed
    assert conn._mock_should_429 is False


def test_429_retry_resets_per_iteration():
    """Each call to iter_files resets the mock, so the 429 fires again."""
    conn = _make_onedrive_conn()
    # First iteration
    files1 = list(conn.iter_files())
    assert len(files1) == 150
    # Second iteration — flag was reset in iter_files
    files2 = list(conn.iter_files())
    assert len(files2) == 150


# ---------------------------------------------------------------------------
# eTag / delta tests
# ---------------------------------------------------------------------------

def test_etag_populated():
    """Every FileRef from iter_files should have a non-empty etag_or_version."""
    conn = _make_onedrive_conn()
    for f in conn.iter_files():
        assert f.etag_or_version, f"Expected non-empty eTag for {f.file_id}"
        assert f.etag_or_version.startswith('"'), \
            f"Graph eTags should be quoted, got: {f.etag_or_version}"


def test_get_change_token_non_empty():
    """get_change_token() should return a non-empty hex string."""
    conn = _make_onedrive_conn()
    token = conn.get_change_token()
    assert token, "Change token should not be empty"
    assert len(token) == 64, f"Expected 64-char SHA-256 hex, got {len(token)} chars"
    # Should be valid hex
    int(token, 16)


def test_get_change_token_stable():
    """get_change_token() should return the same value when no files changed."""
    conn = _make_onedrive_conn()
    token1 = conn.get_change_token()
    token2 = conn.get_change_token()
    assert token1 == token2, "Change token should be stable when data is unchanged"


def test_etag_in_file_metadata():
    """get_file_metadata() should return metadata with content_hash derived from eTag."""
    conn = _make_onedrive_conn()
    # Pick a known mock item
    file_id = "onedrive:item-001-005"
    meta = conn.get_file_metadata(file_id)
    assert meta is not None
    assert meta.content_hash, "content_hash should not be empty"
    assert meta.file_id == file_id
    assert meta.file_name, "file_name should not be empty"
    assert meta.size_bytes > 0, "size_bytes should be positive"


# ---------------------------------------------------------------------------
# file_id format tests
# ---------------------------------------------------------------------------

def test_onedrive_file_id_format():
    """OneDrive FileRef file_id should use 'onedrive:{item_id}' format."""
    conn = _make_onedrive_conn()
    for f in conn.iter_files():
        assert f.file_id.startswith("onedrive:item-"), \
            f"Unexpected file_id format: {f.file_id}"
        break


def test_sharepoint_file_id_format():
    """SharePoint FileRef file_id should use 'sharepoint:{item_id}' format."""
    conn = _make_sharepoint_conn()
    for f in conn.iter_files():
        assert f.file_id.startswith("sharepoint:item-"), \
            f"Unexpected file_id format: {f.file_id}"
        break


def test_all_onedrive_file_ids_prefixed():
    """All OneDrive file_ids should start with 'onedrive:'."""
    conn = _make_onedrive_conn()
    for f in conn.iter_files():
        assert f.file_id.startswith("onedrive:"), \
            f"file_id {f.file_id} does not start with 'onedrive:'"


def test_all_sharepoint_file_ids_prefixed():
    """All SharePoint file_ids should start with 'sharepoint:'."""
    conn = _make_sharepoint_conn()
    for f in conn.iter_files():
        assert f.file_id.startswith("sharepoint:"), \
            f"file_id {f.file_id} does not start with 'sharepoint:'"


# ---------------------------------------------------------------------------
# list_files backward compat
# ---------------------------------------------------------------------------

def test_list_files_same_count_as_iter_files():
    """list_files() should return the same number of items as iter_files()."""
    conn = _make_onedrive_conn()
    iter_count = len(list(conn.iter_files()))
    # list_files() internally calls iter_files() then get_file_metadata()
    list_result = conn.list_files()
    assert len(list_result) == iter_count, \
        f"list_files() returned {len(list_result)}, iter_files() returned {iter_count}"


def test_list_files_returns_filemetadata():
    """list_files() should return FileMetadata objects."""
    conn = _make_sharepoint_conn()
    files = conn.list_files()
    assert len(files) > 0
    for fm in files:
        assert isinstance(fm, FileMetadata), \
            f"Expected FileMetadata, got {type(fm)}"
        assert fm.file_id, "file_id should not be empty"
        assert fm.file_name, "file_name should not be empty"
        assert fm.content_hash, "content_hash should not be empty"


# ---------------------------------------------------------------------------
# get_file_metadata tests
# ---------------------------------------------------------------------------

def test_get_file_metadata_known_item():
    """get_file_metadata() should return a valid FileMetadata for a known mock item."""
    conn = _make_onedrive_conn()
    meta = conn.get_file_metadata("onedrive:item-000-000")
    assert meta is not None
    assert meta.file_name == "document_0000.pdf"
    assert meta.size_bytes == 1024  # (0*50 + 0 + 1) * 1024 = 1024
    assert meta.mime_type == "application/pdf"


def test_get_file_metadata_nonexistent():
    """get_file_metadata() should return None for an unknown item."""
    conn = _make_onedrive_conn()
    meta = conn.get_file_metadata("onedrive:item-999-999")
    assert meta is None


def test_get_file_metadata_without_prefix():
    """get_file_metadata() should also work without the source prefix."""
    conn = _make_onedrive_conn()
    meta = conn.get_file_metadata("item-000-001")
    assert meta is not None
    assert meta.file_id == "item-000-001"


# ---------------------------------------------------------------------------
# download_file tests
# ---------------------------------------------------------------------------

def test_download_file_mock():
    """download_file() should return mock bytes in mock mode."""
    conn = _make_onedrive_conn()
    data = conn.download_file("onedrive:item-000-000")
    assert isinstance(data, bytes)
    assert len(data) > 0
    assert b"mock-content-for-document_0000.pdf" in data


def test_open_file_mock():
    """open_file() should return a readable BinaryIO in mock mode."""
    conn = _make_onedrive_conn()
    # Get a FileRef from iter_files
    file_ref = next(conn.iter_files())
    fh = conn.open_file(file_ref)
    try:
        data = fh.read()
        assert len(data) > 0
        assert isinstance(data, bytes)
        assert file_ref.file_name.encode("utf-8") in data
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# get_owner_hints tests
# ---------------------------------------------------------------------------

def test_onedrive_owner_hints():
    """OneDrive owner hints should include source and drive_owner."""
    conn = _make_onedrive_conn()
    hints = conn.get_owner_hints("onedrive:item-000-000")
    assert hints["source"] == "onedrive"
    assert hints["drive_owner"] == "mock-user@contoso.com"


def test_sharepoint_owner_hints():
    """SharePoint owner hints should include source, site_id, drive_id."""
    conn = _make_sharepoint_conn()
    hints = conn.get_owner_hints("sharepoint:item-000-000")
    assert hints["source"] == "sharepoint"
    assert hints["site_id"] == "mock-site"
    assert hints["drive_id"] == "mock-drive"


# ---------------------------------------------------------------------------
# Edge case: empty file ref has required fields
# ---------------------------------------------------------------------------

def test_file_ref_populated_fields():
    """Each FileRef from mock iter_files should have all required fields populated."""
    conn = _make_onedrive_conn()
    for f in conn.iter_files():
        assert f.file_id
        assert f.file_name
        assert f.path_or_uri
        assert f.source_type == "onedrive"
        assert f.size_bytes > 0
        assert f.last_modified
        assert f.etag_or_version
        assert f.mime_type
        break


def test_varying_mime_types():
    """Mock files should have varying mime types across items."""
    conn = _make_onedrive_conn()
    mime_types = {f.mime_type for f in conn.iter_files()}
    assert len(mime_types) >= 4, \
        f"Expected at least 4 distinct mime types, got {len(mime_types)}: {mime_types}"


# ---------------------------------------------------------------------------
# Reset behavior
# ---------------------------------------------------------------------------

def test_iter_files_resets_mock_state():
    """Each iter_files() call should reset mock counters for a fresh scan."""
    conn = _make_onedrive_conn()
    # First scan
    files1 = list(conn.iter_files())
    assert len(files1) == 150
    # Verify internal state
    assert conn._mock_page == 3
    assert conn._mock_should_429 is False
    # Second scan — state is reset
    files2 = list(conn.iter_files())
    assert len(files2) == 150
    assert conn._mock_page == 3  # Reset, then consumed 3 pages again
