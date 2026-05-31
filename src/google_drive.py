"""Google Drive connector for the GDPR scanning pipeline.

Supports: personal drives, shared drives, Google-native doc export.
Mock mode: credentials_path="mock" uses an in-memory Drive API stub.
"""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import BinaryIO, Iterator, Optional

from .connector import Connector
from .models import FileRef

# ---------------------------------------------------------------------------
# 1. Mock Drive API v3 (offline, no network)
# ---------------------------------------------------------------------------

# Pre-built mock file data: 150 files, 3 pages of 50
# -  0- 49: regular files with md5Checksum (PDF, PNG, DOCX, XLSX)
# - 50- 99: Google Docs (no md5Checksum, has version)
# -100-149: Google Sheets (no md5Checksum, has version)
# Files 100-149 also belong to a shared drive.

_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
_GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_MOCK_SHARED_DRIVE_ID = "mockSharedDrive123"

_MOCK_MIME_TYPES = [
    "application/pdf",
    "image/png",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
]  # cycled for regular files


def _make_mock_file(index: int) -> dict:
    """Build a single mock Drive file dict (API v3 shape)."""
    file_id = f"mock-file-{index:03d}"
    name = f"file_{index:03d}"

    if index < 50:
        # Regular file with md5Checksum
        mime = _MOCK_MIME_TYPES[index % len(_MOCK_MIME_TYPES)]
        ext_map = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        }
        name += ext_map.get(mime, "")
        return {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "size": str(1024 * (index + 1)),
            "modifiedTime": f"2025-0{(index % 9) + 1:01d}-{(index % 28) + 1:02d}T{index % 24:02d}:00:00.000Z",
            "md5Checksum": hashlib.md5(f"content_{index}".encode()).hexdigest(),
            "version": str((index % 10) + 1),
            "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
        }
    elif index < 100:
        # Google Doc (no md5Checksum)
        return {
            "id": file_id,
            "name": name,
            "mimeType": _GOOGLE_DOC_MIME,
            "size": None,
            "modifiedTime": f"2025-0{(index % 9) + 1:01d}-{(index % 28) + 1:02d}T{index % 24:02d}:00:00.000Z",
            "version": str((index % 20) + 1),
            "webViewLink": f"https://docs.google.com/document/d/{file_id}/view",
        }
    else:
        # Google Sheet (no md5Checksum, in shared drive)
        return {
            "id": file_id,
            "name": name,
            "mimeType": _GOOGLE_SHEET_MIME,
            "size": None,
            "modifiedTime": f"2025-0{(index % 9) + 1:01d}-{(index % 28) + 1:02d}T{index % 24:02d}:00:00.000Z",
            "version": str((index % 30) + 1),
            "webViewLink": f"https://docs.google.com/spreadsheets/d/{file_id}/view",
            "sharedDriveId": _MOCK_SHARED_DRIVE_ID,
        }


_MOCK_ALL_FILES = [_make_mock_file(i) for i in range(150)]
_MOCK_FILES_BY_ID = {f["id"]: f for f in _MOCK_ALL_FILES}
_FILE_COUNT_PER_PAGE = 50


def _paginate_mock_files(
    files: list[dict],
    query: str | None = None,
    drive_id: str | None = None,
    shared_drive_id: str | None = None,
    page_token: str | None = None,
    page_size: int = 50,
) -> dict:
    """Simulate Drive API v3 files.list response with paging."""
    result_files = list(files)

    # Shared drive filter
    if shared_drive_id:
        result_files = [f for f in result_files if f.get("sharedDriveId") == shared_drive_id]
    if drive_id:
        result_files = [f for f in result_files if f.get("sharedDriveId") == drive_id]

    # Super-basic query filtering (just for realism; tests don't rely on this)
    if query:
        lower = query.lower()
        if "mimetype" in lower and "google-apps" in lower:
            result_files = [f for f in result_files if "google-apps" in (f.get("mimeType") or "")]
        elif "name" in lower and "contains" in lower:
            # Extract term between single quotes
            import re
            m = re.search(r"'([^']*)'", query)
            if m:
                term = m.group(1).lower()
                result_files = [f for f in result_files if term in f.get("name", "").lower()]

    total = len(result_files)
    if page_token:
        try:
            offset = int(page_token)
        except (ValueError, TypeError):
            offset = 0
    else:
        offset = 0

    page = result_files[offset : offset + page_size]
    next_offset = offset + page_size
    next_token = str(next_offset) if next_offset < total else None

    return {
        "files": page,
        "nextPageToken": next_token,
    }


class _MockHttpRequest:
    """Mimics googleapiclient HttpRequest with a .execute() method."""

    def __init__(self, result):
        self._result = result

    def execute(self):
        if callable(self._result):
            return self._result()
        return self._result


class _MockFilesResource:
    """Mimics service.files() return value."""

    def __init__(self, all_files: list[dict], shared_drive_id: str | None = None):
        self._all = all_files
        self._shared_drive_id = shared_drive_id

    def list(
        self,
        q: str | None = None,
        pageToken: str | None = None,
        pageSize: int = 50,
        fields: str | None = None,
        driveId: str | None = None,
        corpora: str | None = None,
        includeItemsFromAllDrives: bool | None = None,
        supportsAllDrives: bool | None = None,
        **kwargs,
    ):
        """Simulate files().list(...)"""
        drive_id = driveId or self._shared_drive_id
        result = _paginate_mock_files(
            self._all,
            query=q,
            drive_id=drive_id,
            shared_drive_id=drive_id,
            page_token=pageToken,
            page_size=pageSize,
        )
        return _MockHttpRequest(result)

    def get(
        self,
        fileId: str,
        fields: str | None = None,
        **kwargs,
    ):
        """Simulate files().get(fileId=...)"""
        file_data = _MOCK_FILES_BY_ID.get(fileId)
        if file_data is None:
            # Return a 404-like error
            def _raise():
                raise FileNotFoundError(f"Mock file not found: {fileId}")
            return _MockHttpRequest(_raise)
        return _MockHttpRequest(dict(file_data))

    def get_media(self, fileId: str, **kwargs):
        """Simulate files().get_media(fileId=...)"""

        def _execute():
            file_data = _MOCK_FILES_BY_ID.get(fileId)
            if file_data is None:
                raise FileNotFoundError(f"Mock file not found: {fileId}")
            size = int(file_data.get("size", 0) or 0)
            # Generate deterministic content bytes
            content = f"MOCK_CONTENT:{fileId}".encode()
            return content * max(1, size // len(content)) if size else content

        return _MockHttpRequest(_execute)

    def export(self, fileId: str, mimeType: str, **kwargs):
        """Simulate files().export(fileId=..., mimeType=...)"""
        del mimeType  # ignore — we just return PDF-like content

        def _execute():
            file_data = _MOCK_FILES_BY_ID.get(fileId)
            if file_data is None:
                raise FileNotFoundError(f"Mock file not found: {fileId}")
            # Simulated PDF export
            return f"%PDF-1.4 mock export of {fileId}".encode()

        return _MockHttpRequest(_execute)


class _MockPermissionsResource:
    """Mimics service.permissions().list(...)"""

    def list(self, fileId: str, fields: str | None = None, **kwargs):
        def _execute():
            # Return a mock permission entry for the file
            index = 0
            if fileId.startswith("mock-file-"):
                try:
                    index = int(fileId.split("-")[-1])
                except ValueError:
                    pass
            return {
                "permissions": [
                    {
                        "id": f"perm-{fileId}",
                        "type": "user",
                        "role": "owner",
                        "emailAddress": f"owner_{index:03d}@example.com",
                        "displayName": f"Owner {index:03d}",
                    }
                ]
            }

        return _MockHttpRequest(_execute)


class _MockDriveService:
    """Mimics a googleapiclient Drive API v3 service object."""

    def __init__(self, shared_drive_id: str | None = None):
        self._files_resource = _MockFilesResource(_MOCK_ALL_FILES, shared_drive_id)
        self._permissions_resource = _MockPermissionsResource()

    def files(self):
        return self._files_resource

    def permissions(self):
        return self._permissions_resource


# ---------------------------------------------------------------------------
# 2. Real service builder (used when googleapiclient is available)
# ---------------------------------------------------------------------------

def _build_real_service(credentials_path: str):
    """Build a real Google Drive API v3 service from credentials_path."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=scopes
    )
    return build("drive", "v3", credentials=credentials)


# ---------------------------------------------------------------------------
# 3. GoogleDriveConnector
# ---------------------------------------------------------------------------

_FIELDS = (
    "nextPageToken,"
    "files(id,name,mimeType,size,modifiedTime,md5Checksum,"
    "version,webViewLink,sharedDriveId)"
)


class GoogleDriveConnector(Connector):
    """Connector for Google Drive via Drive API v3.

    Supports: personal drives, shared drives, Google-native docs (export).
    """

    def __init__(
        self,
        credentials_path: str,
        shared_drive_id: str | None = None,
        max_concurrent: int = 4,
    ):
        self._credentials_path = credentials_path
        self._shared_drive_id = shared_drive_id
        self._max_concurrent = max_concurrent
        self._service = self._build_service()

    # ------------------------------------------------------------------
    # _build_service
    # ------------------------------------------------------------------

    def _build_service(self):
        """Build the Google Drive API service object (mock or real)."""
        if self._credentials_path == "mock":
            return _MockDriveService(self._shared_drive_id)
        return _build_real_service(self._credentials_path)

    # ------------------------------------------------------------------
    # _list_files_paginated
    # ------------------------------------------------------------------

    def _list_files_paginated(self, query: str | None = None) -> Iterator[dict]:
        """Paginate through Drive API v3 file list using pageToken."""
        list_kwargs: dict = {
            "pageSize": 50,
            "fields": _FIELDS,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if query:
            list_kwargs["q"] = query
        if self._shared_drive_id:
            list_kwargs["driveId"] = self._shared_drive_id
            list_kwargs["corpora"] = "drive"

        files_api = self._service.files()
        page_token: str | None = None

        while True:
            if page_token:
                list_kwargs["pageToken"] = page_token
            response = files_api.list(**list_kwargs).execute()
            for file_data in response.get("files", []):
                yield file_data
            page_token = response.get("nextPageToken")
            if not page_token:
                break

    # ------------------------------------------------------------------
    # _drive_file_to_fileref
    # ------------------------------------------------------------------

    def _drive_file_to_fileref(self, file: dict) -> FileRef:
        """Convert Drive API file metadata to our FileRef.

        Delta fields: id + md5Checksum/version + size + modifiedTime.
        For Google-native docs without md5Checksum: use version + modifiedTime.
        """
        file_id = file["id"]

        # Etag / version logic
        md5 = file.get("md5Checksum", "")
        version = file.get("version", "")
        if md5:
            etag = md5
        elif version:
            etag = version
        else:
            etag = f"{version}_{file.get('modifiedTime', '')}"

        # Size: Google-native docs may have None/absent size
        raw_size = file.get("size")
        if raw_size is not None:
            size_bytes = int(raw_size)
        else:
            size_bytes = 0

        return FileRef(
            file_id=f"googledrive:{file_id}",
            file_name=file.get("name", file_id),
            path_or_uri=file.get("webViewLink", ""),
            source_type="googledrive",
            size_bytes=size_bytes,
            last_modified=file.get("modifiedTime", ""),
            etag_or_version=etag,
            mime_type=file.get("mimeType", "application/octet-stream"),
        )

    # ------------------------------------------------------------------
    # iter_files
    # ------------------------------------------------------------------

    def iter_files(self) -> Iterator[FileRef]:
        """List all files in Drive (or shared drive)."""
        for file_data in self._list_files_paginated():
            yield self._drive_file_to_fileref(file_data)

    # ------------------------------------------------------------------
    # list_files (backward compat)
    # ------------------------------------------------------------------

    def list_files(self):
        """Return FileMetadata list for backward compatibility.

        Makes individual get() calls to compute content_hash.
        """
        from .models import FileMetadata

        results = []
        for file_ref in self.iter_files():
            try:
                metadata = self.get_file_metadata(file_ref.file_id)
                if metadata:
                    results.append(metadata)
            except Exception:
                # Skip files that can't be resolved (e.g. deleted between list and get)
                continue
        return results

    # ------------------------------------------------------------------
    # get_file_metadata
    # ------------------------------------------------------------------

    def get_file_metadata(self, file_id: str):
        """Return metadata for a single file, or None if not found."""
        from .models import FileMetadata

        native_id = self._strip_prefix(file_id)
        files_api = self._service.files()

        try:
            file_data = files_api.get(fileId=native_id, fields=_FIELDS).execute()
        except FileNotFoundError:
            return None

        raw_size = file_data.get("size")
        size_bytes = int(raw_size) if raw_size is not None else 0

        # Compute a content hash for the file
        try:
            media = files_api.get_media(fileId=native_id).execute()
            content_hash = hashlib.sha256(media if isinstance(media, bytes) else str(media).encode()).hexdigest()
        except Exception:
            content_hash = ""

        return FileMetadata(
            file_id=file_id,
            file_name=file_data.get("name", native_id),
            path=file_data.get("webViewLink", ""),
            size_bytes=size_bytes,
            last_modified=file_data.get("modifiedTime", ""),
            content_hash=content_hash,
            mime_type=file_data.get("mimeType", "application/octet-stream"),
        )

    # ------------------------------------------------------------------
    # download_file
    # ------------------------------------------------------------------

    def download_file(self, file_id: str) -> bytes:
        """Return raw bytes of the given file."""
        native_id = self._strip_prefix(file_id)
        files_api = self._service.files()

        file_data = files_api.get(fileId=native_id, fields="mimeType").execute()
        mime_type = file_data.get("mimeType", "")

        if mime_type and "google-apps" in mime_type:
            # Export Google-native doc to PDF
            return files_api.export(fileId=native_id, mimeType="application/pdf").execute()
        else:
            return files_api.get_media(fileId=native_id).execute()

    # ------------------------------------------------------------------
    # open_file
    # ------------------------------------------------------------------

    def open_file(self, file_ref: FileRef) -> BinaryIO:
        """Download/export file content and return as a BytesIO stream."""
        data = self.download_file(file_ref.file_id)
        return io.BytesIO(data)

    # ------------------------------------------------------------------
    # get_owner_hints
    # ------------------------------------------------------------------

    def get_owner_hints(self, file_id: str) -> dict:
        """Return owner info from file permissions."""
        native_id = self._strip_prefix(file_id)
        perms_api = self._service.permissions()

        try:
            response = perms_api.list(
                fileId=native_id,
                fields="permissions(id,type,role,emailAddress,displayName)",
            ).execute()
        except Exception:
            return {}

        owner = {}
        for perm in response.get("permissions", []):
            if perm.get("role") == "owner":
                owner = {
                    "name": perm.get("displayName", ""),
                    "email": perm.get("emailAddress", ""),
                    "department": "",
                    "site_owner": "",
                    "master_of_data": "",
                }
                break

        if not owner:
            # Fallback: use first permission
            first = response.get("permissions", [{}])[0] if response.get("permissions") else {}
            owner = {
                "name": first.get("displayName", ""),
                "email": first.get("emailAddress", ""),
                "department": "",
                "site_owner": "",
                "master_of_data": "",
            }

        return owner

    # ------------------------------------------------------------------
    # get_change_token
    # ------------------------------------------------------------------

    def get_change_token(self) -> str:
        """Hash of the latest modifiedTime across all files."""
        hasher = hashlib.sha256()
        timestamps = []

        for file_data in self._list_files_paginated():
            mt = file_data.get("modifiedTime", "")
            if mt:
                timestamps.append(mt)

        for mt in sorted(timestamps):
            hasher.update(mt.encode())

        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_prefix(file_id: str) -> str:
        """Remove 'googledrive:' prefix if present."""
        prefix = "googledrive:"
        if file_id.startswith(prefix):
            return file_id[len(prefix):]
        return file_id
