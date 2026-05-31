"""Unit tests for GoogleDriveConnector (mock mode, offline)."""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.google_drive import GoogleDriveConnector
from src.models import FileRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector(shared_drive_id=None) -> GoogleDriveConnector:
    return GoogleDriveConnector(
        credentials_path="mock",
        shared_drive_id=shared_drive_id,
    )


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

def test_paging_yields_all_150_files():
    """Three pages of 50 files each — iter_files should yield all 150."""
    conn = _make_connector()
    files = list(conn.iter_files())
    assert len(files) == 150, f"Expected 150 files, got {len(files)}"


def test_iter_files_pages_are_consumed_lazily():
    """iter_files should work as a lazy iterator (not pre-load everything)."""
    conn = _make_connector()
    it = conn.iter_files()
    first = next(it)
    assert isinstance(first, FileRef)
    # The rest should keep coming
    rest = list(it)
    assert len(rest) == 149


# ---------------------------------------------------------------------------
# FileRef fields
# ---------------------------------------------------------------------------

def test_fileref_source_type_is_googledrive():
    """Every FileRef from iter_files must have source_type='googledrive'."""
    conn = _make_connector()
    for f in conn.iter_files():
        assert f.source_type == "googledrive", f"Expected googledrive, got {f.source_type}"


def test_fileref_file_id_format():
    """file_id must follow the pattern 'googledrive:{native_id}'."""
    conn = _make_connector()
    for f in conn.iter_files():
        assert f.file_id.startswith("googledrive:"), f"Bad file_id prefix: {f.file_id}"
        # The native id part should exist and not be empty
        native = f.file_id[len("googledrive:"):]
        assert native, f"Empty native id in {f.file_id}"
        assert native.startswith("mock-file-"), f"Unexpected native id: {native}"


def test_fileref_has_required_fields():
    """Each FileRef must have all required fields populated."""
    conn = _make_connector()
    first = next(conn.iter_files())
    assert first.file_id
    assert first.file_name
    assert first.path_or_uri  # webViewLink
    assert first.source_type == "googledrive"
    assert first.size_bytes >= 0
    assert first.last_modified
    assert first.etag_or_version


# ---------------------------------------------------------------------------
# Etag / version logic
# ---------------------------------------------------------------------------

def test_regular_file_etag_from_md5checksum():
    """Regular files (with md5Checksum) should use it as etag_or_version."""
    conn = _make_connector()
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index < 50:
            # Regular file — etag should be a 32-char hex md5
            assert len(f.etag_or_version) == 32, (
                f"Expected 32-char md5 etag for {f.file_id}, got '{f.etag_or_version}'"
            )
            assert all(c in "0123456789abcdef" for c in f.etag_or_version.lower()), (
                f"Expected hex etag for {f.file_id}, got '{f.etag_or_version}'"
            )


def test_google_doc_etag_from_version_no_md5checksum():
    """Google Docs lack md5Checksum — etag must come from version field."""
    conn = _make_connector()
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if 50 <= index < 100:
            # Google Doc — etag should be a version string (numeric-ish)
            assert f.etag_or_version, f"Empty etag for Google Doc {f.file_id}"
            # Should not be a 32-char hex md5 (that would mean we fell through to md5)
            assert not (
                len(f.etag_or_version) == 32
                and all(c in "0123456789abcdef" for c in f.etag_or_version.lower())
            ), f"Google Doc {f.file_id} got md5-style etag but shouldn't have one"


def test_google_sheet_etag_from_version():
    """Google Sheets lack md5Checksum — etag must come from version field."""
    conn = _make_connector()
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index >= 100:
            assert f.etag_or_version, f"Empty etag for Google Sheet {f.file_id}"
            # Should not be a 32-char hex md5
            assert not (
                len(f.etag_or_version) == 32
                and all(c in "0123456789abcdef" for c in f.etag_or_version.lower())
            ), f"Google Sheet {f.file_id} got md5-style etag but shouldn't have one"


# ---------------------------------------------------------------------------
# Shared drive
# ---------------------------------------------------------------------------

def test_shared_drive_files_discoverable():
    """When shared_drive_id is set, only shared drive files are returned."""
    conn = _make_connector(shared_drive_id="mockSharedDrive123")
    files = list(conn.iter_files())
    assert len(files) > 0, "Expected at least some shared drive files"
    assert len(files) == 50, f"Expected 50 shared drive files, got {len(files)}"
    for f in files:
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        assert index >= 100, f"File {f.file_id} should be in shared drive range"


def test_all_files_discoverable_without_shared_drive_id():
    """Without shared_drive_id, all 150 files (including shared drive) are returned."""
    conn = _make_connector()
    files = list(conn.iter_files())
    assert len(files) == 150


# ---------------------------------------------------------------------------
# get_change_token
# ---------------------------------------------------------------------------

def test_get_change_token_returns_non_empty_string():
    """get_change_token must produce a non-empty hex string."""
    conn = _make_connector()
    token = conn.get_change_token()
    assert token, "Change token should not be empty"
    assert isinstance(token, str)
    # Should be a sha256 hex digest (64 chars)
    assert len(token) == 64, f"Expected 64-char hex, got {len(token)}: {token}"


def test_get_change_token_is_deterministic():
    """Same connector config should produce the same token."""
    conn1 = _make_connector()
    conn2 = _make_connector()
    assert conn1.get_change_token() == conn2.get_change_token()


# ---------------------------------------------------------------------------
# list_files (backward compat)
# ---------------------------------------------------------------------------

def test_list_files_returns_same_count_as_iter_files():
    """list_files() should return the same number of entries as iter_files()."""
    conn = _make_connector()
    iter_count = len(list(conn.iter_files()))
    list_count = len(conn.list_files())
    assert list_count == iter_count, (
        f"list_files returned {list_count}, iter_files returned {iter_count}"
    )


# ---------------------------------------------------------------------------
# get_file_metadata
# ---------------------------------------------------------------------------

def test_get_file_metadata_returns_metadata_for_valid_id():
    """get_file_metadata should work for a valid file_id."""
    conn = _make_connector()
    first = next(conn.iter_files())
    meta = conn.get_file_metadata(first.file_id)
    assert meta is not None
    assert meta.file_id == first.file_id
    assert meta.file_name == first.file_name


# ---------------------------------------------------------------------------
# open_file — regular file download
# ---------------------------------------------------------------------------

def test_open_file_handles_regular_file_download():
    """open_file should return a readable BytesIO for a regular file."""
    conn = _make_connector()
    # Find a regular file (index < 50)
    regular = None
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index < 50:
            regular = f
            break
    assert regular is not None, "No regular file found"

    fh = conn.open_file(regular)
    try:
        assert hasattr(fh, "read"), "Returned object should have read()"
        data = fh.read()
        assert isinstance(data, bytes)
        assert len(data) > 0, "Should read some content"
        assert b"MOCK_CONTENT" in data, f"Expected mock content marker, got {data[:50]}"
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# open_file — Google doc export to PDF
# ---------------------------------------------------------------------------

def test_open_file_handles_google_doc_export():
    """open_file should export a Google Doc as PDF-like content."""
    conn = _make_connector()
    # Find a Google Doc (50 <= index < 100)
    doc = None
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if 50 <= index < 100:
            doc = f
            break
    assert doc is not None, "No Google Doc found"

    fh = conn.open_file(doc)
    try:
        data = fh.read()
        assert isinstance(data, bytes)
        assert len(data) > 0
        # Mock export returns PDF-like content
        assert b"%PDF" in data, f"Expected PDF header for exported doc, got {data[:50]}"
    finally:
        fh.close()


def test_open_file_handles_google_sheet_export():
    """open_file should export a Google Sheet as PDF-like content."""
    conn = _make_connector()
    # Find a Google Sheet (index >= 100)
    sheet = None
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index >= 100:
            sheet = f
            break
    assert sheet is not None, "No Google Sheet found"

    fh = conn.open_file(sheet)
    try:
        data = fh.read()
        assert isinstance(data, bytes)
        assert len(data) > 0
        assert b"%PDF" in data, f"Expected PDF header for exported sheet, got {data[:50]}"
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# get_owner_hints
# ---------------------------------------------------------------------------

def test_get_owner_hints_returns_dict():
    """get_owner_hints should return a non-empty dict for a valid file."""
    conn = _make_connector()
    first = next(conn.iter_files())
    hints = conn.get_owner_hints(first.file_id)
    assert isinstance(hints, dict)
    assert "name" in hints
    assert "email" in hints
    assert hints.get("email"), "Owner email should not be empty"


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

def test_download_file_returns_bytes():
    """download_file should return bytes for a regular file."""
    conn = _make_connector()
    first = next(conn.iter_files())
    data = conn.download_file(first.file_id)
    assert isinstance(data, bytes)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# MIME types
# ---------------------------------------------------------------------------

def test_mime_types_are_preserved():
    """FileRef.mime_type should match the Drive file's mimeType."""
    conn = _make_connector()
    for f in conn.iter_files():
        assert f.mime_type, f"Empty mime_type for {f.file_id}"
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index < 50:
            assert f.mime_type in (
                "application/pdf",
                "image/png",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ), f"Unexpected mime_type for regular file: {f.mime_type}"
        elif index < 100:
            assert f.mime_type == "application/vnd.google-apps.document"
        else:
            assert f.mime_type == "application/vnd.google-apps.spreadsheet"


# ---------------------------------------------------------------------------
# File size handling
# ---------------------------------------------------------------------------

def test_google_native_docs_have_zero_size():
    """Google Docs and Sheets report size=None from API — should become 0."""
    conn = _make_connector()
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index >= 50:
            # Google-native docs have no meaningful file size in bytes
            assert f.size_bytes == 0, (
                f"Google-native file {f.file_id} should have size 0, got {f.size_bytes}"
            )


def test_regular_files_have_positive_size():
    """Regular files should have a positive size."""
    conn = _make_connector()
    for f in conn.iter_files():
        native = f.file_id[len("googledrive:"):]
        index = int(native.split("-")[-1])
        if index < 50:
            assert f.size_bytes > 0, f"Regular file {f.file_id} should have positive size"


# ---------------------------------------------------------------------------
# Abstract methods completeness
# ---------------------------------------------------------------------------

def test_all_abstract_methods_implemented():
    """GoogleDriveConnector must implement all Connector ABC methods."""
    from src.connector import Connector

    abstract_methods = [
        "list_files", "iter_files", "get_file_metadata",
        "download_file", "open_file", "get_owner_hints", "get_change_token",
    ]
    for method_name in abstract_methods:
        method = getattr(GoogleDriveConnector, method_name, None)
        assert method is not None, f"Missing method: {method_name}"
        # Must not be the ABC's abstract method stub
        assert not hasattr(method, "__isabstractmethod__") or not method.__isabstractmethod__, (
            f"Method {method_name} is still abstract"
        )


# ---------------------------------------------------------------------------
# Instantiation check
# ---------------------------------------------------------------------------

def test_can_instantiate_with_mock():
    """GoogleDriveConnector with credentials_path='mock' must instantiate."""
    conn = _make_connector()
    assert conn is not None
    assert isinstance(conn, GoogleDriveConnector)
