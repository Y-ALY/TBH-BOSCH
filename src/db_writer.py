"""Bulk DB writer — accumulates findings and file metadata, flushes in batches.

Used by the streaming scanner and background scan jobs to avoid per-finding
individual INSERT/UPDATE calls to SQLite.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import List

from sqlalchemy.orm import Session

from .models import Finding, FileRef

logger = logging.getLogger(__name__)


class BulkWriter:
    """Accumulates findings and file state records, flushes in batches for efficient DB writes.

    Tracks flush count and total write time. Auto-flushes when the batch size
    is reached.
    """

    def __init__(self, db_session: Session, batch_size: int = 500):
        self._db = db_session
        self._batch_size = batch_size

        self._file_states: list[dict] = []
        self._findings: list[Finding] = []

        self._existing_finding_uids: set[str] = set()
        self._flush_count: int = 0
        self._total_write_time_ms: float = 0.0
        self._total_rows_written: int = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def add_file_state(self, file_ref: FileRef, content_hash: str = "") -> None:
        """Queue a file state record for bulk upsert.

        The record is held in memory; it is not written until ``flush()``
        is called (or the batch size triggers an auto-flush).
        """
        from database import FileMetadata as FileMetadataORM

        # Check whether this path already exists in the DB
        existing = (
            self._db.query(FileMetadataORM)
            .filter(FileMetadataORM.file_path == file_ref.path_or_uri)
            .first()
        )

        if existing:
            # Update in place
            existing.size_bytes = file_ref.size_bytes
            try:
                existing.last_modified = datetime.fromisoformat(file_ref.last_modified)
            except (ValueError, TypeError):
                existing.last_modified = datetime.now()
            if content_hash:
                existing.file_hash = content_hash
            self._db.flush()
            return

        row = {
            "file_path": file_ref.path_or_uri,
            "owner_employee_id": "BX-17335",
            "size_bytes": file_ref.size_bytes,
            "file_hash": content_hash,
            "last_modified": self._parse_datetime(file_ref.last_modified),
            "retention_deadline": datetime.now() + timedelta(days=200),
        }
        self._file_states.append(row)

        if self._total_queued > self._batch_size:
            self.flush()

    def add_finding(self, finding: Finding) -> None:
        """Queue a finding for bulk insert/upsert.

        If a finding with the same ``finding_uid`` already exists in the DB,
        it will be updated instead of inserted on flush.
        """
        self._findings.append(finding)

        if self._total_queued > self._batch_size:
            self.flush()

    def flush(self) -> int:
        """Write all queued records to the database.

        Returns the total number of rows written (inserted + updated).
        """
        from database import FileMetadata as FileMetadataORM
        from database import Finding as FindingORM

        rows_written = 0
        t0 = time.perf_counter()

        # ── File state records ──────────────────────────────────────────
        if self._file_states:
            self._db.bulk_insert_mappings(FileMetadataORM, self._file_states)
            rows_written += len(self._file_states)
            self._file_states.clear()

        # ── Findings (with upsert) ──────────────────────────────────────
        if self._findings:
            # Collect all finding_uids in this batch
            batch_uids = {f.finding_id for f in self._findings}

            # Query which ones already exist in the DB
            if batch_uids:
                existing_rows = (
                    self._db.query(FindingORM)
                    .filter(FindingORM.finding_uid.in_(batch_uids))
                    .all()
                )
                existing_map: dict[str, FindingORM] = {
                    r.finding_uid: r for r in existing_rows if r.finding_uid
                }

                # Split into inserts and updates
                to_insert: list[dict] = []
                for f in self._findings:
                    if f.finding_id in existing_map:
                        # Update existing
                        orm_row = existing_map[f.finding_id]
                        self._apply_finding_to_orm(f, orm_row)
                        rows_written += 1
                    else:
                        to_insert.append(self._finding_to_row(f))

                if to_insert:
                    self._db.bulk_insert_mappings(FindingORM, to_insert)
                    rows_written += len(to_insert)

            self._findings.clear()

        self._db.flush()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._flush_count += 1
        self._total_write_time_ms += elapsed_ms
        self._total_rows_written += rows_written

        logger.debug(
            "BulkWriter flush #%d: %d rows in %.1f ms",
            self._flush_count,
            rows_written,
            elapsed_ms,
        )

        return rows_written

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of queued items not yet flushed."""
        return self._total_queued

    @property
    def _total_queued(self) -> int:
        return len(self._file_states) + len(self._findings)

    @property
    def flush_count(self) -> int:
        """Number of flushes performed so far."""
        return self._flush_count

    @property
    def total_write_time_ms(self) -> float:
        """Cumulative time spent in flush() calls, in milliseconds."""
        return self._total_write_time_ms

    @property
    def total_rows_written(self) -> int:
        """Total rows inserted or updated across all flushes."""
        return self._total_rows_written

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(iso_string: str) -> datetime:
        try:
            return datetime.fromisoformat(iso_string)
        except (ValueError, TypeError):
            return datetime.now()

    @staticmethod
    def _finding_to_row(f: Finding) -> dict:
        return {
            "finding_uid": f.finding_id,
            "file_id": None,
            "file_id_str": f.file_id,
            "type": f.type,
            "value": f.value,
            "field": f.field,
            "context": f.context,
            "risk_level": f.risk_level,
            "confidence": f.confidence,
            "evidence": f.evidence,
            "recommended_action": f.recommended_action,
            "assigned_owner": f.assigned_owner,
            "owner_email": f.owner_email,
            "owner_department": f.owner_department,
            "owner_resolved": f.owner_resolved,
            "escalation_target": f.escalation_target,
            "category": f.type,
            "confidence_score": f.confidence,
            "flagged_snippet": f.value,
            "reasoning": f.context,
            "status": "Pending",
            "review_status": "pending",
        }

    @staticmethod
    def _apply_finding_to_orm(f: Finding, orm_row) -> None:
        """Copy Finding dataclass fields onto an existing ORM row (in-place update)."""
        orm_row.type = f.type
        orm_row.value = f.value
        orm_row.field = f.field
        orm_row.context = f.context
        orm_row.risk_level = f.risk_level
        orm_row.confidence = f.confidence
        orm_row.evidence = f.evidence
        orm_row.recommended_action = f.recommended_action
        orm_row.assigned_owner = f.assigned_owner
        orm_row.owner_email = f.owner_email
        orm_row.owner_department = f.owner_department
        orm_row.owner_reserved = f.owner_resolved
        orm_row.escalation_target = f.escalation_target
        orm_row.category = f.type
        orm_row.confidence_score = f.confidence
        orm_row.flagged_snippet = f.value
        orm_row.reasoning = f.context
