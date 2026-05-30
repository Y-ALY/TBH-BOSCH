"""Unified connector interface and local demo implementation.

ABC Connector defines 5 methods that any source must implement.
LocalSampleRepoConnector reads PDFs from a local directory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import hashlib
import json

from .models import FileMetadata


class Connector(ABC):
    """Abstract connector — the contract any data source must fulfill."""

    @abstractmethod
    def list_files(self) -> list[FileMetadata]:
        """Return metadata for all discoverable files in the source."""
        ...

    @abstractmethod
    def get_file_metadata(self, file_id: str) -> FileMetadata | None:
        """Return metadata for a single file, or None if not found."""
        ...

    @abstractmethod
    def download_file(self, file_id: str) -> bytes:
        """Return raw bytes of the given file."""
        ...

    @abstractmethod
    def get_owner_hints(self, file_id: str) -> dict:
        """Return owner-hint dict for a file (name, email, department, site_owner, master_of_data)."""
        ...

    @abstractmethod
    def get_change_token(self) -> str:
        """Return an opaque token representing the current state of the source.

        Used as a coarse check: if the token is unchanged, no files changed.
        For delta scan, individual file hashes are still the ground truth.
        """
        ...


class LocalSampleRepoConnector(Connector):
    """Reads PDF files from a local directory. Demo connector for the hackathon.

    Owner hints are loaded from a JSON file mapping file_id → hint dict:

        {
          "expense_001.pdf": {
            "name": "Anna Schmidt",
            "email": "anna.schmidt@bosch.com",
            "department": "Finance",
            "site_owner": "Dr. Mueller",
            "master_of_data": "CDO Office"
          }
        }
    """

    def __init__(self, repo_path: str, owner_hints_file: str | None = None):
        self.repo_path = Path(repo_path).resolve()
        self._owner_hints: dict[str, dict] = {}
        if owner_hints_file:
            hints_path = Path(owner_hints_file)
            if hints_path.exists():
                with open(hints_path) as f:
                    self._owner_hints = json.load(f)

    # ------------------------------------------------------------------
    # list_files
    # ------------------------------------------------------------------

    def list_files(self) -> list[FileMetadata]:
        if not self.repo_path.exists():
            return []

        files = []
        for pdf_path in sorted(self.repo_path.glob("*.pdf")):
            try:
                stat = pdf_path.stat()
                content_hash = self._hash_file(pdf_path)
                files.append(FileMetadata(
                    file_id=f"local:{pdf_path.name}",
                    file_name=pdf_path.name,
                    path=str(pdf_path),
                    size_bytes=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    content_hash=content_hash,
                ))
            except OSError:
                # Skip files we cannot read (permissions, etc.)
                continue

        return files

    # ------------------------------------------------------------------
    # get_file_metadata
    # ------------------------------------------------------------------

    def get_file_metadata(self, file_id: str) -> FileMetadata | None:
        file_name = self._resolve_file_id(file_id)
        if file_name is None:
            return None
        pdf_path = self.repo_path / file_name
        if not pdf_path.exists():
            return None
        stat = pdf_path.stat()
        return FileMetadata(
            file_id=file_id,
            file_name=file_name,
            path=str(pdf_path),
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            content_hash=self._hash_file(pdf_path),
        )

    # ------------------------------------------------------------------
    # download_file
    # ------------------------------------------------------------------

    def download_file(self, file_id: str) -> bytes:
        file_name = self._resolve_file_id(file_id)
        if file_name is None:
            raise FileNotFoundError(f"Cannot resolve file_id: {file_id}")
        pdf_path = self.repo_path / file_name
        return pdf_path.read_bytes()

    # ------------------------------------------------------------------
    # get_owner_hints
    # ------------------------------------------------------------------

    def get_owner_hints(self, file_id: str) -> dict:
        file_name = self._resolve_file_id(file_id)
        return self._owner_hints.get(file_name or file_id, {})

    # ------------------------------------------------------------------
    # get_change_token
    # ------------------------------------------------------------------

    def get_change_token(self) -> str:
        """Hash of (name, mtime, size) for all PDFs — a cheap change fingerprint."""
        hasher = hashlib.sha256()
        for pdf_path in sorted(self.repo_path.glob("*.pdf")):
            try:
                stat = pdf_path.stat()
                token = f"{pdf_path.name}|{stat.st_mtime}|{stat.st_size}"
                hasher.update(token.encode())
            except OSError:
                continue
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _resolve_file_id(self, file_id: str) -> str | None:
        """Strip 'local:' prefix if present, return the bare file name."""
        if file_id.startswith("local:"):
            return file_id[len("local:"):]
        return file_id

    @staticmethod
    def _hash_file(path: Path) -> str:
        """SHA-256 hex digest of a file's contents."""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
