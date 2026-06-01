"""Normalize file_path values so DB rows work on Render and locally."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from database import FileMetadata


def rewrite_strict_drive_paths(db: Session, project_root: Path | None = None) -> int:
    """Point stored paths at ./strict_drive under the current project root."""
    root = (project_root or Path.cwd()).resolve()
    updated = 0
    for row in db.query(FileMetadata).all():
        raw = (row.file_path or "").replace("\\", "/")
        marker = "strict_drive/"
        idx = raw.find(marker)
        if idx < 0:
            continue
        rel = raw[idx:]
        new_path = str((root / rel).resolve())
        if row.file_path != new_path:
            row.file_path = new_path
            updated += 1
    if updated:
        db.commit()
    return updated
