"""Microsoft Graph connectors for OneDrive and SharePoint.

Provides MicrosoftGraphConnector base class with auth, paging, retry/backoff,
and mock mode for offline testing. Two concrete implementations:
- OneDriveConnector: scans a user's OneDrive for Business
- SharePointConnector: scans a SharePoint site's document library
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from abc import abstractmethod
from datetime import datetime
from typing import BinaryIO, Iterator

import requests

from .connector import Connector
from .models import FileMetadata, FileRef


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/png": ".png",
}


def _ext_for_mime(mime: str) -> str:
    return _MIME_TO_EXT.get(mime, ".bin")


class _MockRetrySignal(Exception):
    """Raised internally to simulate a 429/5xx in mock mode."""


def _generate_mock_items(page: int, count: int = 50) -> list[dict]:
    """Generate synthetic Graph driveItem dicts for one page of mock data."""
    mime_types = list(_MIME_TO_EXT.keys())
    items = []
    for i in range(count):
        idx = page * count + i
        mime = mime_types[idx % len(mime_types)]
        ext = _ext_for_mime(mime)
        items.append({
            "id": f"item-{page:03d}-{i:03d}",
            "name": f"document_{idx:04d}{ext}",
            "webUrl": f"https://contoso.sharepoint.com/sites/test/Documents/document_{idx:04d}{ext}",
            "size": (idx + 1) * 1024,
            "lastModifiedDateTime": f"2024-01-{(idx % 28) + 1:02d}T{(idx % 24):02d}:00:00Z",
            "eTag": f"\"mock-etag-{idx:04d},{{idx:04d}}\"",
            "file": {"mimeType": mime},
        })
    return items


def _parse_mock_item_id(raw_item_id: str) -> tuple[int, int] | None:
    """Parse a mock item id like 'item-002-015' into (page, index). Returns None on failure."""
    if not raw_item_id.startswith("item-"):
        return None
    parts = raw_item_id[5:].split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _build_mock_item(page: int, index: int) -> dict | None:
    """Build a single mock driveItem for the given page/index."""
    mime_types = list(_MIME_TO_EXT.keys())
    count = 50
    idx = page * count + index
    if index < 0 or index >= count:
        return None
    mime = mime_types[idx % len(mime_types)]
    ext = _ext_for_mime(mime)
    return {
        "id": f"item-{page:03d}-{index:03d}",
        "name": f"document_{idx:04d}{ext}",
        "webUrl": f"https://contoso.sharepoint.com/sites/test/Documents/document_{idx:04d}{ext}",
        "size": (idx + 1) * 1024,
        "lastModifiedDateTime": f"2024-01-{(idx % 28) + 1:02d}T{(idx % 24):02d}:00:00Z",
        "eTag": f"\"mock-etag-{idx:04d},{{idx:04d}}\"",
        "file": {"mimeType": mime},
    }


# ---------------------------------------------------------------------------
# MicrosoftGraphConnector — base
# ---------------------------------------------------------------------------

class MicrosoftGraphConnector(Connector):
    """Base connector for Microsoft Graph API (OneDrive + SharePoint).

    Handles: auth, paging, retry/backoff, metadata delta.
    Set client_id=\"mock\" to run without network access.
    """

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"
    MAX_RETRIES = 5
    MOCK_PAGES = 3
    MOCK_PAGE_SIZE = 50

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 max_concurrent: int = 4):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.max_concurrent = max_concurrent
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

        # Mock mode
        self._mock_mode = (client_id == "mock")

        # Mock state: simulate one 429 on the first request
        self._mock_should_429 = True
        # Mock page counter for paginated listing
        self._mock_page = 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _authenticate(self) -> str:
        """Get OAuth2 client-credentials token for Microsoft Graph."""
        if self._mock_mode:
            return "mock-access-token"

        now = time.time()
        if self._access_token and now < self._token_expiry - 60:
            return self._access_token

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        resp = requests.post(token_url, data=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = now + data.get("expires_in", 3600)
        return self._access_token

    # ------------------------------------------------------------------
    # HTTP layer with retry
    # ------------------------------------------------------------------

    def _graph_request(self, endpoint: str, params: dict | None = None) -> dict:
        """Make a request to Microsoft Graph with exponential-backoff retry.

        Retries on 429 (rate-limit) and 5xx errors up to MAX_RETRIES times.
        Backoff: 1s, 2s, 4s, 8s, 16s.
        """
        backoff = 1.0
        for attempt in range(1 + self.MAX_RETRIES):
            try:
                if self._mock_mode:
                    return self._mock_graph_call(endpoint, params)
                return self._real_graph_call(endpoint, params)
            except _MockRetrySignal:
                if attempt < self.MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise RuntimeError(
                    f"Mock retries exhausted for {endpoint}"
                )
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (429,) or status >= 500:
                    if attempt < self.MAX_RETRIES:
                        retry_after = backoff
                        if exc.response is not None:
                            try:
                                retry_after = int(exc.response.headers.get("Retry-After", backoff))
                            except (ValueError, TypeError):
                                pass
                        time.sleep(retry_after)
                        backoff = max(backoff * 2, retry_after)
                        continue
                raise
            except requests.RequestException:
                if attempt < self.MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise

        raise RuntimeError(f"Max retries exceeded for endpoint: {endpoint}")

    def _real_graph_call(self, endpoint: str, params: dict | None = None) -> dict:
        """Execute a real HTTP GET against Microsoft Graph."""
        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._authenticate()}",
            "Accept": "application/json",
        }
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _mock_graph_call(self, endpoint: str, params: dict | None = None) -> dict:
        """Return synthetic Graph API responses for offline testing."""
        # Simulate one 429 on the very first request
        if self._mock_should_429:
            self._mock_should_429 = False
            raise _MockRetrySignal()

        page = self._mock_page
        self._mock_page += 1

        items = _generate_mock_items(page, self.MOCK_PAGE_SIZE)
        result: dict = {"value": items}
        if page < self.MOCK_PAGES - 1:
            result["@odata.nextLink"] = (
                f"{self.GRAPH_BASE}/mock/nextLink?page={page + 1}"
            )

        return result

    # ------------------------------------------------------------------
    # Paging
    # ------------------------------------------------------------------

    def _graph_paginated(self, endpoint: str, params: dict | None = None) -> Iterator[dict]:
        """Yield items from paginated Graph API responses.

        Follows @odata.nextLink until no more pages are available.
        """
        current_endpoint = endpoint
        current_params = dict(params) if params else None

        while True:
            response = self._graph_request(current_endpoint, current_params)
            for item in response.get("value", []):
                yield item

            next_link = response.get("@odata.nextLink")
            if not next_link:
                break

            # nextLink is a full URL — strip the base to get the endpoint + query
            if next_link.startswith(self.GRAPH_BASE):
                relative = next_link[len(self.GRAPH_BASE):]
            else:
                relative = next_link

            if "?" in relative:
                current_endpoint, query = relative.split("?", 1)
                current_params = {}
                for part in query.split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        current_params[k] = v
            else:
                current_endpoint = relative
                current_params = None

    # ------------------------------------------------------------------
    # Item conversion
    # ------------------------------------------------------------------

    def _drive_item_to_fileref(self, item: dict) -> FileRef:
        """Convert a Graph driveItem dict into our FileRef model."""
        item_id = item["id"]
        file_info = item.get("file", {})
        mime_type = file_info.get("mimeType", "application/octet-stream")
        return FileRef(
            file_id=self._make_file_id(item_id),
            file_name=item.get("name", "unknown"),
            path_or_uri=item.get("webUrl", ""),
            source_type=self._source_type(),
            size_bytes=item.get("size", 0),
            last_modified=item.get("lastModifiedDateTime", ""),
            etag_or_version=item.get("eTag", ""),
            mime_type=mime_type,
        )

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    def _source_type(self) -> str:
        """Return 'onedrive' or 'sharepoint'."""
        ...

    @abstractmethod
    def _make_file_id(self, item_id: str) -> str:
        """Prefix an item id with the source type, e.g. 'onedrive:abc123'."""
        ...

    @abstractmethod
    def _get_list_endpoint(self) -> str:
        """Return the Graph endpoint for listing files."""
        ...

    @abstractmethod
    def _get_content_endpoint(self, item_id: str) -> str:
        """Return the Graph endpoint for downloading file content."""
        ...

    @abstractmethod
    def _get_item_endpoint(self, item_id: str) -> str:
        """Return the Graph endpoint for a single driveItem."""
        ...

    # ------------------------------------------------------------------
    # Connector ABC — iter_files
    # ------------------------------------------------------------------

    def iter_files(self) -> Iterator[FileRef]:
        """Streaming file discovery — yields lightweight FileRef objects.

        Lists files from the configured drive endpoint with automatic paging.
        Does NOT download content. Uses metadata-before-download pattern.
        """
        # Reset mock page counter for fresh iteration
        self._mock_page = 0
        self._mock_should_429 = True

        endpoint = self._get_list_endpoint()
        for item in self._graph_paginated(endpoint):
            yield self._drive_item_to_fileref(item)

    # ------------------------------------------------------------------
    # Connector ABC — open_file
    # ------------------------------------------------------------------

    def open_file(self, file_ref: FileRef) -> BinaryIO:
        """Stream a file's contents from Microsoft Graph.

        Returns a BytesIO buffer so callers get a consistent BinaryIO interface.
        Callers must close the returned handle.
        """
        item_id = self._parse_file_id(file_ref.file_id)
        if self._mock_mode:
            item = self._resolve_mock_item(item_id)
            name = item.get("name", "unknown") if item else file_ref.file_name
            content = f"mock-content-for-{name}".encode("utf-8")
            return io.BytesIO(content)

        endpoint = self._get_content_endpoint(item_id)
        response = self._graph_request(endpoint)
        # The content endpoint returns raw bytes, but _graph_request parses JSON.
        # We need to fetch the raw content separately.
        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._authenticate()}",
        }
        resp = requests.get(url, headers=headers, timeout=120)
        resp.raise_for_status()
        return io.BytesIO(resp.content)

    # ------------------------------------------------------------------
    # Connector ABC — download_file
    # ------------------------------------------------------------------

    def download_file(self, file_id: str) -> bytes:
        """Return raw bytes of the given file.

        Metadata-before-download: callers should check eTag before invoking.
        """
        item_id = self._parse_file_id(file_id)
        if self._mock_mode:
            item = self._resolve_mock_item(item_id)
            name = item.get("name", "unknown") if item else file_id
            return f"mock-content-for-{name}".encode("utf-8")

        endpoint = self._get_content_endpoint(item_id)
        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._authenticate()}",
        }
        resp = requests.get(url, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # Connector ABC — get_file_metadata
    # ------------------------------------------------------------------

    def get_file_metadata(self, file_id: str) -> FileMetadata | None:
        """Return metadata for a single file, or None if not found."""
        item_id = self._parse_file_id(file_id)
        if self._mock_mode:
            item = self._resolve_mock_item(item_id)
            if item is None:
                return None
        else:
            endpoint = self._get_item_endpoint(item_id)
            try:
                item = self._graph_request(endpoint)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    return None
                raise

        file_info = item.get("file", {})
        content_hash = hashlib.sha256(
            f"{item.get('eTag', '')}{item.get('size', 0)}".encode()
        ).hexdigest()
        return FileMetadata(
            file_id=file_id,
            file_name=item.get("name", "unknown"),
            path=item.get("webUrl", ""),
            size_bytes=item.get("size", 0),
            last_modified=item.get("lastModifiedDateTime", ""),
            content_hash=content_hash,
            mime_type=file_info.get("mimeType", "application/octet-stream"),
        )

    # ------------------------------------------------------------------
    # Connector ABC — get_owner_hints
    # ------------------------------------------------------------------

    def get_owner_hints(self, file_id: str) -> dict:
        """Return owner-hint dict for a file."""
        return {"source": self._source_type()}

    # ------------------------------------------------------------------
    # Connector ABC — get_change_token
    # ------------------------------------------------------------------

    def get_change_token(self) -> str:
        """Return an opaque token representing the current state of the source.

        Hashes all file eTags to produce a stable fingerprint. If any file's
        eTag changes, the token will differ.
        """
        hasher = hashlib.sha256()
        for file_ref in self.iter_files():
            hasher.update(file_ref.etag_or_version.encode())
            hasher.update(file_ref.size_bytes.to_bytes(8, "big"))
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # Connector ABC — list_files (backward compat)
    # ------------------------------------------------------------------

    def list_files(self) -> list[FileMetadata]:
        """Return metadata for all discoverable files (backward compat).

        Calls iter_files() and converts each FileRef to FileMetadata.
        """
        result = []
        for file_ref in self.iter_files():
            meta = self.get_file_metadata(file_ref.file_id)
            if meta is not None:
                result.append(meta)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_file_id(self, file_id: str) -> str:
        """Strip the source-type prefix, returning the raw item id."""
        prefix = f"{self._source_type()}:"
        if file_id.startswith(prefix):
            return file_id[len(prefix):]
        return file_id

    def _resolve_mock_item(self, item_id: str) -> dict | None:
        """Look up a mock driveItem by id. Returns None if not found."""
        parsed = _parse_mock_item_id(item_id)
        if parsed is None:
            return None
        page, index = parsed
        return _build_mock_item(page, index)


# ---------------------------------------------------------------------------
# OneDriveConnector
# ---------------------------------------------------------------------------

class OneDriveConnector(MicrosoftGraphConnector):
    """Scans a user's OneDrive for Business.

    Constructor: OneDriveConnector(tenant_id, client_id, client_secret, user_id)
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 user_id: str, max_concurrent: int = 4):
        super().__init__(tenant_id, client_id, client_secret, max_concurrent)
        self.user_id = user_id

    def _source_type(self) -> str:
        return "onedrive"

    def _make_file_id(self, item_id: str) -> str:
        return f"onedrive:{item_id}"

    def _get_list_endpoint(self) -> str:
        return f"/users/{self.user_id}/drive/root/children"

    def _get_content_endpoint(self, item_id: str) -> str:
        return f"/users/{self.user_id}/drive/items/{item_id}/content"

    def _get_item_endpoint(self, item_id: str) -> str:
        return f"/users/{self.user_id}/drive/items/{item_id}"

    def get_owner_hints(self, file_id: str) -> dict:
        return {"source": "onedrive", "drive_owner": self.user_id}


# ---------------------------------------------------------------------------
# SharePointConnector
# ---------------------------------------------------------------------------

class SharePointConnector(MicrosoftGraphConnector):
    """Scans a SharePoint site's document library.

    Constructor: SharePointConnector(tenant_id, client_id, client_secret, site_id, drive_id)
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 site_id: str, drive_id: str, max_concurrent: int = 4):
        super().__init__(tenant_id, client_id, client_secret, max_concurrent)
        self.site_id = site_id
        self.drive_id = drive_id

    def _source_type(self) -> str:
        return "sharepoint"

    def _make_file_id(self, item_id: str) -> str:
        return f"sharepoint:{item_id}"

    def _get_list_endpoint(self) -> str:
        return f"/sites/{self.site_id}/drives/{self.drive_id}/root/children"

    def _get_content_endpoint(self, item_id: str) -> str:
        return f"/sites/{self.site_id}/drives/{self.drive_id}/items/{item_id}/content"

    def _get_item_endpoint(self, item_id: str) -> str:
        return f"/sites/{self.site_id}/drives/{self.drive_id}/items/{item_id}"

    def get_owner_hints(self, file_id: str) -> dict:
        return {
            "source": "sharepoint",
            "site_id": self.site_id,
            "drive_id": self.drive_id,
        }
