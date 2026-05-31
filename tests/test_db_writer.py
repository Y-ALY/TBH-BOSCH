"""Tests for BulkWriter, ScanJob, and ScanError models.

Verifies:
- BulkWriter batches at 500 items
- flush writes to DB
- Upsert (duplicate finding_uid updates instead of inserting)
- Auto-flush when buffer full
- Scan job CRUD operations
"""

from __future__ import annotations

import os
import sys
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ScanJob as ScanJobORM, ScanError as ScanErrorORM, Finding as FindingORM
from src.db_writer import BulkWriter
from src.models import Finding, FileRef


# ---------------------------------------------------------------------------
# Test setup — in-memory SQLite
# ---------------------------------------------------------------------------

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _init_db():
    """Drop and recreate all tables in the in-memory DB."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)


def _db_session():
    """Yield a fresh session."""
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# BulkWriter — batch size
# ---------------------------------------------------------------------------

def test_bulkwriter_batches_at_500():
    """BulkWriter should auto-flush when 500 items are queued."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=500)

    # Adding 500 findings should NOT auto-flush
    for i in range(500):
        f = Finding(
            finding_id=f"test-batch-500-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 500
    assert writer.flush_count == 0  # Not flushed yet

    # The 501st should trigger an auto-flush
    f = Finding(
        finding_id="test-batch-500-500",
        file_id="local:test.pdf",
        type="email",
        value="user500@example.com",
    )
    writer.add_finding(f)

    assert writer.flush_count == 1, f"Expected 1 auto-flush, got {writer.flush_count}"
    assert writer.pending_count == 1  # Only the last item remains

    writer.flush()
    db.commit()
    db.close()


def test_bulkwriter_custom_batch_size():
    """BulkWriter should respect custom batch_size."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=10)

    for i in range(10):
        f = Finding(
            finding_id=f"test-custom-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 10
    assert writer.flush_count == 0

    # 11th triggers auto-flush
    f = Finding(
        finding_id="test-custom-10",
        file_id="local:test.pdf",
        type="email",
        value="user10@example.com",
    )
    writer.add_finding(f)

    assert writer.flush_count == 1
    writer.flush()
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# BulkWriter — flush writes to DB
# ---------------------------------------------------------------------------

def test_bulkwriter_flush_writes_findings():
    """Flush should write findings to the database."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    for i in range(25):
        f = Finding(
            finding_id=f"test-flush-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    rows = writer.flush()
    db.commit()

    assert rows == 25, f"Expected 25 rows written, got {rows}"
    assert writer.pending_count == 0
    assert writer.total_rows_written == 25

    # Verify in DB
    for i in range(25):
        finding = db.query(FindingORM).filter(FindingORM.finding_uid == f"test-flush-{i}").first()
        assert finding is not None, f"Finding test-flush-{i} not found in DB"
        assert finding.value == f"user{i}@example.com"

    db.close()


def test_bulkwriter_flush_resets_buffers():
    """After flush, pending_count should be 0 and buffers should be empty."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    for i in range(30):
        f = Finding(
            finding_id=f"test-reset-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    writer.flush()
    db.commit()

    assert writer.pending_count == 0

    # Adding more should start fresh
    for i in range(5):
        f = Finding(
            finding_id=f"test-reset2-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user2-{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 5

    writer.flush()
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# BulkWriter — upsert behavior
# ---------------------------------------------------------------------------

def test_bulkwriter_upsert_updates_existing():
    """When finding_uid already exists, flush should update instead of inserting duplicate."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    # First insert
    f1 = Finding(
        finding_id="test-upsert-1",
        file_id="local:test.pdf",
        type="email",
        value="original@example.com",
        risk_level="low",
    )
    writer.add_finding(f1)
    writer.flush()
    db.commit()

    # Verify first insert
    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-upsert-1").first()
    assert row is not None
    assert row.value == "original@example.com"
    assert row.risk_level == "low"

    # Now add the same finding_uid with different values (should update)
    f2 = Finding(
        finding_id="test-upsert-1",
        file_id="local:test.pdf",
        type="email",
        value="updated@example.com",
        risk_level="high",
    )
    writer.add_finding(f2)
    rows = writer.flush()
    db.commit()

    # The row should have been updated (written counts as an update)
    assert rows > 0

    # Verify it was updated, not duplicated
    all_rows = db.query(FindingORM).filter(FindingORM.finding_uid == "test-upsert-1").all()
    assert len(all_rows) == 1, f"Expected 1 row, got {len(all_rows)} (upsert should not create duplicates)"
    assert all_rows[0].value == "updated@example.com"
    assert all_rows[0].risk_level == "high"

    db.close()


def test_bulkwriter_upsert_mixed_batch():
    """Mixed batch of new + existing findings should handle both correctly."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    # Pre-populate some findings
    for i in range(5):
        f = Finding(
            finding_id=f"test-mixed-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"old{i}@example.com",
        )
        writer.add_finding(f)
    writer.flush()
    db.commit()

    # Now add a mix: 3 new + 2 existing
    # Existing: test-mixed-0, test-mixed-2
    # New: test-mixed-10, test-mixed-11, test-mixed-12
    batch = [
        Finding(finding_id="test-mixed-0", file_id="local:test.pdf", type="email", value="updated0@example.com"),
        Finding(finding_id="test-mixed-10", file_id="local:test.pdf", type="email", value="new10@example.com"),
        Finding(finding_id="test-mixed-2", file_id="local:test.pdf", type="email", value="updated2@example.com"),
        Finding(finding_id="test-mixed-11", file_id="local:test.pdf", type="email", value="new11@example.com"),
        Finding(finding_id="test-mixed-12", file_id="local:test.pdf", type="email", value="new12@example.com"),
    ]
    for f in batch:
        writer.add_finding(f)
    writer.flush()
    db.commit()

    # Verify updated ones
    r0 = db.query(FindingORM).filter(FindingORM.finding_uid == "test-mixed-0").first()
    assert r0.value == "updated0@example.com"

    r2 = db.query(FindingORM).filter(FindingORM.finding_uid == "test-mixed-2").first()
    assert r2.value == "updated2@example.com"

    # Verify new ones exist
    for uid in ["test-mixed-10", "test-mixed-11", "test-mixed-12"]:
        r = db.query(FindingORM).filter(FindingORM.finding_uid == uid).first()
        assert r is not None, f"{uid} should exist"
        assert "new" in r.value

    # Verify no duplicates
    for uid in ["test-mixed-0", "test-mixed-1", "test-mixed-2", "test-mixed-10", "test-mixed-11", "test-mixed-12"]:
        count = db.query(FindingORM).filter(FindingORM.finding_uid == uid).count()
        assert count <= 1, f"{uid} has {count} rows, expected at most 1"

    db.close()


# ---------------------------------------------------------------------------
# BulkWriter — file_state handling
# ---------------------------------------------------------------------------

def test_bulkwriter_add_file_state():
    """add_file_state should queue file state records and upsert existing ones."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    fr = FileRef(
        file_id="local:test_fs.pdf",
        file_name="test_fs.pdf",
        path_or_uri="/tmp/test_fs.pdf",
        source_type="local",
        size_bytes=1024,
        last_modified="2024-01-01T00:00:00",
        etag_or_version="v1",
    )

    writer.add_file_state(fr, content_hash="abc123")
    writer.flush()
    db.commit()

    # Verify in DB
    from database import FileMetadata as FileMetadataORM
    row = db.query(FileMetadataORM).filter(FileMetadataORM.file_path == "/tmp/test_fs.pdf").first()
    assert row is not None
    assert row.file_hash == "abc123"
    assert row.size_bytes == 1024

    # Add again — should update existing, not insert duplicate
    fr2 = FileRef(
        file_id="local:test_fs.pdf",
        file_name="test_fs.pdf",
        path_or_uri="/tmp/test_fs.pdf",
        source_type="local",
        size_bytes=2048,
        last_modified="2024-01-01T00:00:00",
        etag_or_version="v2",
    )
    writer.add_file_state(fr2, content_hash="def456")
    writer.flush()
    db.commit()

    rows = db.query(FileMetadataORM).filter(FileMetadataORM.file_path == "/tmp/test_fs.pdf").all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)} (should not duplicate)"
    assert rows[0].size_bytes == 2048
    assert rows[0].file_hash == "def456"

    db.close()


# ---------------------------------------------------------------------------
# ScanJob CRUD
# ---------------------------------------------------------------------------

def test_scanjob_create_and_read():
    """Should be able to create a ScanJob and read it back."""
    _init_db()
    db = next(_db_session())

    scan_id = f"test-job-{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()

    job = ScanJobORM(
        scan_id=scan_id,
        status="pending",
        created_at=now,
        options_json='{"mode":"delta","ai_mode":"layered"}',
    )
    db.add(job)
    db.commit()

    # Read back
    found = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    assert found is not None
    assert found.status == "pending"
    assert found.created_at == now

    # Parse options
    opts = json.loads(found.options_json)
    assert opts["mode"] == "delta"
    assert opts["ai_mode"] == "layered"

    db.close()


def test_scanjob_update_status():
    """Should be able to update a ScanJob's status through its lifecycle."""
    _init_db()
    db = next(_db_session())

    scan_id = f"test-lifecycle-{uuid.uuid4().hex[:8]}"

    # Create
    job = ScanJobORM(scan_id=scan_id, status="pending", created_at=datetime.now().isoformat())
    db.add(job)
    db.commit()

    # Update to running
    job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    job.status = "running"
    job.started_at = datetime.now().isoformat()
    job.total_files = 100
    db.commit()

    found = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    assert found.status == "running"
    assert found.total_files == 100
    assert found.started_at is not None

    # Update to completed
    job.status = "completed"
    job.completed_at = datetime.now().isoformat()
    job.files_scanned = 95
    job.files_error = 5
    job.total_findings = 42
    job.metrics_json = '{"total_time_ms":5000,"db_write_time_ms":200}'
    db.commit()

    found = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    assert found.status == "completed"
    assert found.files_scanned == 95
    assert found.files_error == 5
    assert found.total_findings == 42
    assert found.completed_at is not None

    metrics = json.loads(found.metrics_json)
    assert metrics["total_time_ms"] == 5000
    assert metrics["db_write_time_ms"] == 200

    db.close()


def test_scanjob_failed_status():
    """A failed scan should record the error message."""
    _init_db()
    db = next(_db_session())

    scan_id = f"test-failed-{uuid.uuid4().hex[:8]}"
    job = ScanJobORM(scan_id=scan_id, status="pending", created_at=datetime.now().isoformat())
    db.add(job)
    db.commit()

    job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    job.status = "failed"
    job.error_message = "Permission denied: /data/secret"
    job.completed_at = datetime.now().isoformat()
    db.commit()

    found = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    assert found.status == "failed"
    assert found.error_message == "Permission denied: /data/secret"

    db.close()


# ---------------------------------------------------------------------------
# ScanError CRUD
# ---------------------------------------------------------------------------

def test_scanerror_create_and_read():
    """Should be able to create scan errors and list them."""
    _init_db()
    db = next(_db_session())

    scan_id = f"test-errors-{uuid.uuid4().hex[:8]}"

    # Create a scan job first
    job = ScanJobORM(scan_id=scan_id, status="completed", created_at=datetime.now().isoformat())
    db.add(job)
    db.commit()

    # Add some errors
    errors_data = [
        {"file_id": "local:bad1.pdf", "file_name": "bad1.pdf", "error_type": "parse_error", "message": "Corrupt PDF"},
        {"file_id": "local:bad2.pdf", "file_name": "bad2.pdf", "error_type": "permission_denied", "message": "Access denied"},
        {"file_id": "local:bad3.pdf", "file_name": "bad3.pdf", "error_type": "timeout", "message": "Download timed out"},
    ]
    for ed in errors_data:
        e = ScanErrorORM(scan_id=scan_id, **ed)
        db.add(e)
    db.commit()

    # List errors for this scan
    errors = db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == scan_id).all()
    assert len(errors) == 3

    # Verify ordering and content
    types = {e.error_type for e in errors}
    assert types == {"parse_error", "permission_denied", "timeout"}

    # Test pagination
    page1 = db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == scan_id).offset(0).limit(2).all()
    assert len(page1) == 2

    page2 = db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == scan_id).offset(2).limit(10).all()
    assert len(page2) == 1

    db.close()


def test_scanerror_isolated_per_scan():
    """ScanErrors should be isolated per scan_id."""
    _init_db()
    db = next(_db_session())

    sid1 = f"scan-err-a-{uuid.uuid4().hex[:8]}"
    sid2 = f"scan-err-b-{uuid.uuid4().hex[:8]}"

    # Create jobs
    db.add(ScanJobORM(scan_id=sid1, status="completed", created_at=datetime.now().isoformat()))
    db.add(ScanJobORM(scan_id=sid2, status="completed", created_at=datetime.now().isoformat()))
    db.commit()

    # Add errors for each
    db.add(ScanErrorORM(scan_id=sid1, file_id="local:a.pdf", file_name="a.pdf", error_type="parse_error", message="bad a"))
    db.add(ScanErrorORM(scan_id=sid1, file_id="local:b.pdf", file_name="b.pdf", error_type="parse_error", message="bad b"))
    db.add(ScanErrorORM(scan_id=sid2, file_id="local:c.pdf", file_name="c.pdf", error_type="timeout", message="slow c"))
    db.commit()

    assert db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == sid1).count() == 2
    assert db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == sid2).count() == 1
    assert db.query(ScanErrorORM).count() == 3

    db.close()


# ---------------------------------------------------------------------------
# BulkWriter — timing tracking
# ---------------------------------------------------------------------------

def test_bulkwriter_tracks_write_time():
    """BulkWriter should track cumulative write time and flush count."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    assert writer.total_write_time_ms == 0.0
    assert writer.flush_count == 0
    assert writer.total_rows_written == 0

    # Add findings and flush multiple times
    for batch in range(3):
        for i in range(50):
            f = Finding(
                finding_id=f"test-time-{batch}-{i}",
                file_id="local:test.pdf",
                type="email",
                value=f"user{batch}-{i}@example.com",
            )
            writer.add_finding(f)

    assert writer.flush_count == 2  # Auto-flushed at items 51 and 101 (50 remain buffered)
    assert writer.total_rows_written == 100  # 2 * 50 flushed so far
    assert writer.total_write_time_ms > 0.0

    # Flush remaining
    writer.flush()
    db.commit()
    assert writer.total_rows_written == 150
    db.close()


# ---------------------------------------------------------------------------
# BulkWriter — file_state batch handling
# ---------------------------------------------------------------------------

def test_bulkwriter_file_state_batch():
    """File state records should also trigger auto-flush when combined with findings."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=20)

    # Add 10 findings and 10 file states = 20 total, should NOT auto-flush
    for i in range(10):
        f = Finding(
            finding_id=f"test-fs-batch-f-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    for i in range(10):
        fr = FileRef(
            file_id=f"local:fs-{i}.pdf",
            file_name=f"fs-{i}.pdf",
            path_or_uri=f"/tmp/fs-{i}.pdf",
            source_type="local",
            size_bytes=100,
            last_modified="2024-01-01T00:00:00",
            etag_or_version="v1",
        )
        writer.add_file_state(fr, content_hash=f"hash{i}")

    assert writer.pending_count == 20
    assert writer.flush_count == 0

    # One more triggers auto-flush
    f = Finding(
        finding_id="test-fs-batch-f-10",
        file_id="local:test.pdf",
        type="email",
        value="user10@example.com",
    )
    writer.add_finding(f)

    assert writer.flush_count == 1

    writer.flush()
    db.commit()
    db.close()


def test_bulkwriter_pending_count_property():
    """pending_count should equal the number of queued items."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=100)

    assert writer.pending_count == 0

    for i in range(7):
        f = Finding(
            finding_id=f"test-count-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 7

    writer.flush()
    assert writer.pending_count == 0

    db.commit()
    db.close()


# =============================================================================
# Tests using conftest.py fixtures (engine, db_session)
# =============================================================================
# These tests reuse the session-scoped in-memory SQLite engine and
# function-scoped SAVEPOINT-isolated sessions from tests/conftest.py.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Review state persistence (direct ORM, no BulkWriter)
# ---------------------------------------------------------------------------

def test_review_state_persist_new_finding(db_session):
    """Review fields (review_action, review_status, reviewer, reviewed_at)
    should persist on a newly inserted Finding row."""
    f = FindingORM(
        finding_uid="test-review-new",
        file_id_str="local:review_test.pdf",
        type="email",
        value="review-me@example.com",
        risk_level="high",
        review_status="pending_review",
        review_action=None,
        reviewer=None,
        reviewed_at=None,
    )
    db_session.add(f)
    db_session.commit()

    row = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-new"
    ).first()
    assert row is not None
    assert row.review_status == "pending_review"
    assert row.review_action is None
    assert row.reviewer is None
    assert row.reviewed_at is None


def test_review_state_update_existing(db_session):
    """Updating review fields on an existing finding should persist changes."""
    # Insert initial finding
    f = FindingORM(
        finding_uid="test-review-update",
        file_id_str="local:review_test.pdf",
        type="email",
        value="update-me@example.com",
        review_status="pending_review",
        review_action=None,
        reviewer=None,
        reviewed_at=None,
    )
    db_session.add(f)
    db_session.commit()

    # Simulate a human review action
    row = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-update"
    ).first()
    row.review_status = "retained"
    row.review_action = "retain"
    row.reviewer = "BX-ADMIN"
    row.reviewed_at = "2026-06-01T12:00:00"
    db_session.commit()

    # Re-read and verify
    row2 = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-update"
    ).first()
    assert row2.review_status == "retained"
    assert row2.review_action == "retain"
    assert row2.reviewer == "BX-ADMIN"
    assert row2.reviewed_at == "2026-06-01T12:00:00"


def test_review_state_full_lifecycle(db_session):
    """Review state should transition through pending -> retained -> deleted."""
    f = FindingORM(
        finding_uid="test-review-lifecycle",
        file_id_str="local:lifecycle.pdf",
        type="tax_id",
        value="DE999999999",
        review_status="pending_review",
    )
    db_session.add(f)
    db_session.commit()

    # pending_review
    row = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-lifecycle"
    ).first()
    assert row.review_status == "pending_review"

    # retained
    row.review_status = "retained"
    row.review_action = "retain"
    row.reviewer = "reviewer-1"
    row.reviewed_at = "2026-06-01T10:00:00"
    db_session.commit()

    row2 = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-lifecycle"
    ).first()
    assert row2.review_status == "retained"
    assert row2.reviewer == "reviewer-1"

    # deleted
    row2.review_status = "deleted"
    row2.review_action = "delete"
    row2.reviewer = "reviewer-2"
    row2.reviewed_at = "2026-06-01T11:00:00"
    db_session.commit()

    row3 = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-lifecycle"
    ).first()
    assert row3.review_status == "deleted"
    assert row3.review_action == "delete"
    assert row3.reviewer == "reviewer-2"


def test_review_state_defaults(db_session):
    """New findings should default to review_status='pending_review' and
    review_action/reviewer/reviewed_at as NULL/None."""
    f = FindingORM(
        finding_uid="test-review-defaults",
        file_id_str="local:defaults.pdf",
        type="name",
        value="Default User",
    )
    db_session.add(f)
    db_session.commit()

    row = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "test-review-defaults"
    ).first()
    assert row.review_status == "pending_review"
    assert row.review_action is None
    assert row.reviewer is None
    assert row.reviewed_at is None


# ---------------------------------------------------------------------------
# Finding insert / update via BulkWriter (conftest fixtures)
# ---------------------------------------------------------------------------

def test_bulkwriter_finding_insert_conftest(db_session):
    """BulkWriter.add_finding + flush should insert new findings."""
    writer = BulkWriter(db_session, batch_size=50)

    for i in range(10):
        f_obj = Finding(
            finding_id=f"conftest-insert-{i}",
            file_id="local:conftest.pdf",
            type="email",
            value=f"conftest{i}@example.com",
            risk_level="medium",
        )
        writer.add_finding(f_obj)

    rows = writer.flush()
    db_session.commit()

    assert rows == 10
    assert writer.pending_count == 0

    for i in range(10):
        row = db_session.query(FindingORM).filter(
            FindingORM.finding_uid == f"conftest-insert-{i}"
        ).first()
        assert row is not None, f"conftest-insert-{i} not found"
        assert row.value == f"conftest{i}@example.com"


def test_bulkwriter_finding_upsert_conftest(db_session):
    """Adding a finding with an existing finding_uid should UPDATE, not duplicate."""
    writer = BulkWriter(db_session, batch_size=50)

    # Insert
    f1 = Finding(
        finding_id="conftest-upsert",
        file_id="local:conftest.pdf",
        type="email",
        value="first@example.com",
        risk_level="low",
    )
    writer.add_finding(f1)
    writer.flush()
    db_session.commit()

    # Verify first insert
    row = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "conftest-upsert"
    ).first()
    assert row is not None
    assert row.value == "first@example.com"
    assert row.risk_level == "low"

    # Upsert with same finding_uid
    f2 = Finding(
        finding_id="conftest-upsert",
        file_id="local:conftest.pdf",
        type="email",
        value="second@example.com",
        risk_level="high",
    )
    writer.add_finding(f2)
    writer.flush()
    db_session.commit()

    # Should be exactly one row, with updated values
    all_rows = db_session.query(FindingORM).filter(
        FindingORM.finding_uid == "conftest-upsert"
    ).all()
    assert len(all_rows) == 1
    assert all_rows[0].value == "second@example.com"
    assert all_rows[0].risk_level == "high"


# ---------------------------------------------------------------------------
# File metadata insert / update via BulkWriter (conftest fixtures)
# ---------------------------------------------------------------------------

def test_bulkwriter_file_metadata_insert_conftest(db_session):
    """add_file_state should insert a new FileMetadata row when the path is new."""
    writer = BulkWriter(db_session, batch_size=50)

    fr = FileRef(
        file_id="local:conftest_file.pdf",
        file_name="conftest_file.pdf",
        path_or_uri="/tmp/conftest_file.pdf",
        source_type="local",
        size_bytes=4096,
        last_modified="2025-01-15T08:30:00",
        etag_or_version="etag-abc",
    )
    writer.add_file_state(fr, content_hash="sha256-fff")
    writer.flush()
    db_session.commit()

    from database import FileMetadata as FileMetadataORM
    row = db_session.query(FileMetadataORM).filter(
        FileMetadataORM.file_path == "/tmp/conftest_file.pdf"
    ).first()
    assert row is not None
    assert row.file_hash == "sha256-fff"
    assert row.size_bytes == 4096
    # DEMO-ONLY default owner
    assert row.owner_employee_id == "BX-17335"


def test_bulkwriter_file_metadata_update_conftest(db_session):
    """Adding the same file path again should UPDATE the existing row, not duplicate."""
    writer = BulkWriter(db_session, batch_size=50)

    fr1 = FileRef(
        file_id="local:conftest_update.pdf",
        file_name="conftest_update.pdf",
        path_or_uri="/tmp/conftest_update.pdf",
        source_type="local",
        size_bytes=1000,
        last_modified="2025-01-01T00:00:00",
        etag_or_version="v1",
    )
    writer.add_file_state(fr1, content_hash="hash-aaa")
    writer.flush()
    db_session.commit()

    # Update same path with new values
    fr2 = FileRef(
        file_id="local:conftest_update.pdf",
        file_name="conftest_update.pdf",
        path_or_uri="/tmp/conftest_update.pdf",
        source_type="local",
        size_bytes=2000,
        last_modified="2025-06-01T12:00:00",
        etag_or_version="v2",
    )
    writer.add_file_state(fr2, content_hash="hash-bbb")
    writer.flush()
    db_session.commit()

    from database import FileMetadata as FileMetadataORM
    rows = db_session.query(FileMetadataORM).filter(
        FileMetadataORM.file_path == "/tmp/conftest_update.pdf"
    ).all()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)} (should update, not duplicate)"
    assert rows[0].size_bytes == 2000
    assert rows[0].file_hash == "hash-bbb"


# ---------------------------------------------------------------------------
# BulkWriter flush semantics (conftest fixtures)
# ---------------------------------------------------------------------------

def test_bulkwriter_flush_clears_buffers_conftest(db_session):
    """After flush, pending_count returns to 0 and new items start fresh."""
    writer = BulkWriter(db_session, batch_size=100)

    for i in range(15):
        f = Finding(
            finding_id=f"conftest-flush-buf-{i}",
            file_id="local:conftest.pdf",
            type="email",
            value=f"buf{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 15
    writer.flush()
    db_session.commit()

    assert writer.pending_count == 0

    # Second batch
    for i in range(5):
        f = Finding(
            finding_id=f"conftest-flush-buf2-{i}",
            file_id="local:conftest.pdf",
            type="email",
            value=f"buf2-{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 5
    writer.flush()
    db_session.commit()
    assert writer.pending_count == 0


def test_bulkwriter_autoflush_on_batch_full_conftest(db_session):
    """When pending_count reaches batch_size, an auto-flush should be triggered."""
    writer = BulkWriter(db_session, batch_size=10)

    for i in range(10):
        f = Finding(
            finding_id=f"conftest-autoflush-{i}",
            file_id="local:conftest.pdf",
            type="email",
            value=f"auto{i}@example.com",
        )
        writer.add_finding(f)

    assert writer.pending_count == 10
    assert writer.flush_count == 0, "Should not flush when exactly at batch_size"

    # The 11th item triggers auto-flush
    f = Finding(
        finding_id="conftest-autoflush-10",
        file_id="local:conftest.pdf",
        type="email",
        value="auto10@example.com",
    )
    writer.add_finding(f)

    assert writer.flush_count >= 1, f"Expected auto-flush, got flush_count={writer.flush_count}"

    writer.flush()
    db_session.commit()


def test_bulkwriter_tracks_metrics_conftest(db_session):
    """flush_count, total_rows_written, and total_write_time_ms should be tracked."""
    writer = BulkWriter(db_session, batch_size=20)

    assert writer.flush_count == 0
    assert writer.total_rows_written == 0
    assert writer.total_write_time_ms == 0.0

    for i in range(25):
        f = Finding(
            finding_id=f"conftest-metrics-{i}",
            file_id="local:conftest.pdf",
            type="email",
            value=f"metrics{i}@example.com",
        )
        writer.add_finding(f)

    # 25 items with batch_size=20: auto-flush at item 21, 4 remain
    assert writer.flush_count == 1
    assert writer.total_rows_written == 20

    writer.flush()
    db_session.commit()

    assert writer.total_rows_written == 25
    assert writer.total_write_time_ms > 0.0
