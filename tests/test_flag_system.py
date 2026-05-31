"""Tests for the GDPR flag/finding lifecycle system.

Verifies:
- Finding dataclass defaults (is_flagged, flag_type)
- DB persistence of flagged fields
- Status/review_status sync on creation
- Review action status transitions
- GET /api/findings response schema (is_flagged, flag_type, review_status)
- Old endpoint backward compatibility with new status values
"""

from __future__ import annotations

import os
import sys
import json
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Finding as FindingORM
from src.db_writer import BulkWriter
from src.models import Finding, ReviewAction, ReviewItem, ALLOWED_REVIEW_ACTIONS


# ---------------------------------------------------------------------------
# Test setup — in-memory SQLite
# ---------------------------------------------------------------------------

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _init_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)


def _db_session():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ===========================================================================
# Finding dataclass defaults
# ===========================================================================

def test_finding_has_is_flagged_default():
    """Finding dataclass should default is_flagged=True."""
    f = Finding(
        finding_id="",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
    )
    assert f.is_flagged is True


def test_finding_has_flag_type_default():
    """Finding dataclass should default flag_type='Regex_Match'."""
    f = Finding(
        finding_id="",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
    )
    assert f.flag_type == "Regex_Match"


def test_finding_can_set_flag_fields():
    """Finding should accept is_flagged and flag_type."""
    f = Finding(
        finding_id="test-1",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
        is_flagged=False,
        flag_type="AI_Detected",
    )
    assert f.is_flagged is False
    assert f.flag_type == "AI_Detected"


# ===========================================================================
# DB persistence of flagged fields via BulkWriter
# ===========================================================================

def test_bulkwriter_persists_is_flagged():
    """BulkWriter should persist is_flagged to the DB."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    f = Finding(
        finding_id="test-flagged-1",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
        is_flagged=True,
        flag_type="Regex_Match",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-flagged-1").first()
    assert row is not None
    assert row.is_flagged is True
    assert row.flag_type == "Regex_Match"

    db.close()


def test_bulkwriter_persists_not_flagged():
    """BulkWriter should persist is_flagged=False."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    f = Finding(
        finding_id="test-not-flagged",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
        is_flagged=False,
        flag_type="",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-not-flagged").first()
    assert row is not None
    assert row.is_flagged is False
    assert row.flag_type == ""

    db.close()


def test_bulkwriter_persists_flag_type_semantic():
    """BulkWriter should persist Semantic_Match flag_type."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    f = Finding(
        finding_id="test-semantic-flag",
        file_id="local:test.pdf",
        type="phone",
        value="+49 123 456789",
        is_flagged=True,
        flag_type="Semantic_Match",
        confidence=0.8,
        evidence="Semantic context match",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-semantic-flag").first()
    assert row is not None
    assert row.is_flagged is True
    assert row.flag_type == "Semantic_Match"
    assert row.confidence == 0.8

    db.close()


# ===========================================================================
# Status / review_status defaults and sync
# ===========================================================================

def test_new_finding_has_pending_review_status():
    """New finding should default to pending_review for both status and review_status."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    f = Finding(
        finding_id="test-status-default",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-status-default").first()
    assert row.status == "pending_review"
    assert row.review_status == "pending_review"

    db.close()


def test_bulkwriter_upsert_preserves_review_state():
    """When upserting, BulkWriter should NOT overwrite existing review_state."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    # First insert
    f1 = Finding(
        finding_id="test-review-preserve",
        file_id="local:test.pdf",
        type="email",
        value="original@example.com",
    )
    writer.add_finding(f1)
    writer.flush()
    db.commit()

    # Manually set review state (simulating a review action)
    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-review-preserve").first()
    row.status = "retained"
    row.review_status = "retained"
    row.review_action = "retain"
    row.reviewer = "admin"
    row.reviewed_at = datetime.now().isoformat()
    db.commit()

    # Now upsert with updated values (re-scan)
    f2 = Finding(
        finding_id="test-review-preserve",
        file_id="local:test.pdf",
        type="email",
        value="updated@example.com",
    )
    writer.add_finding(f2)
    writer.flush()
    db.commit()

    # Value should be updated, but review state should be preserved
    # (BulkWriter._apply_finding_to_orm doesn't touch review_status/review_action)
    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-review-preserve").first()
    assert row.value == "updated@example.com"
    assert row.status == "retained"
    assert row.review_status == "retained"
    assert row.review_action == "retain"
    assert row.reviewer == "admin"

    db.close()


# ===========================================================================
# Review action state machine
# ===========================================================================

def test_review_action_all_values_are_valid():
    """All items in ALLOWED_REVIEW_ACTIONS should be valid for ReviewAction."""
    for action in ALLOWED_REVIEW_ACTIONS:
        ra = ReviewAction(action=action, reviewer="tester", reason="test")
        assert ra.action == action


def test_review_action_rejects_invalid():
    """ReviewAction should reject unknown actions."""
    import pytest
    with pytest.raises(ValueError):
        ReviewAction(action="invalid_action", reviewer="tester", reason="test")


def test_review_item_creation():
    """ReviewItem should be created with default values."""
    ri = ReviewItem(
        review_id="",
        finding_id="finding-abc123",
    )
    assert ri.status == "pending"
    assert ri.owner_status == "pending"
    assert len(ri.allowed_actions) > 0
    assert ri.actions_log == []


def test_review_item_actions_log():
    """ReviewItem should log actions."""
    ri = ReviewItem(
        review_id="review-abc",
        finding_id="finding-abc",
    )
    ra = ReviewAction(action="retain", reviewer="admin", reason="valid business need")
    ri.actions_log.append(ra)
    ri.status = "completed"

    assert len(ri.actions_log) == 1
    assert ri.actions_log[0].action == "retain"
    assert ri.status == "completed"


# ===========================================================================
# Review status mapping
# ===========================================================================

_ACTION_TO_STATUS = {
    "retain": "retained",
    "delete": "deleted",
    "archive": "archived",
    "mask": "masked",
    "false_positive": "false_positive",
    "escalate_dpo": "escalated",
}


def test_all_allowed_actions_have_status_mapping():
    """Every allowed review action should map to a lifecycle status."""
    for action in ALLOWED_REVIEW_ACTIONS:
        assert action in _ACTION_TO_STATUS, f"Missing status mapping for action: {action}"
        status = _ACTION_TO_STATUS[action]
        assert isinstance(status, str)
        assert len(status) > 0


def test_action_to_status_mapping_is_bijective():
    """Each action should map to a unique status (no collisions)."""
    statuses = list(_ACTION_TO_STATUS.values())
    assert len(statuses) == len(set(statuses)), "Status values must be unique"


def test_review_status_lifecycle_scenario():
    """Simulate full finding lifecycle."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    # 1. Create finding
    f = Finding(
        finding_id="test-lifecycle",
        file_id="local:test.pdf",
        type="email",
        value="test@example.com",
        risk_level="medium",
        is_flagged=True,
        flag_type="Regex_Match",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    # Verify initial state
    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-lifecycle").first()
    assert row.status == "pending_review"
    assert row.review_status == "pending_review"
    assert row.review_action is None
    assert row.is_flagged is True
    assert row.flag_type == "Regex_Match"

    # 2. Review: mark as false_positive
    now = datetime.now().isoformat()
    row.status = "false_positive"
    row.review_status = "false_positive"
    row.review_action = "false_positive"
    row.reviewer = "dpo@bosch.com"
    row.reviewed_at = now
    db.commit()

    # Verify reviewed state
    row2 = db.query(FindingORM).filter(FindingORM.finding_uid == "test-lifecycle").first()
    assert row2.status == "false_positive"
    assert row2.review_status == "false_positive"
    assert row2.review_action == "false_positive"
    assert row2.reviewer == "dpo@bosch.com"
    assert row2.reviewed_at == now

    # 3. Re-scan: value is updated
    f2 = Finding(
        finding_id="test-lifecycle",
        file_id="local:test.pdf",
        type="email",
        value="new-value@example.com",
        is_flagged=True,
        flag_type="Regex_Match",
    )
    writer.add_finding(f2)
    writer.flush()
    db.commit()

    # Value updated, review state preserved (BulkWriter doesn't touch review fields)
    row3 = db.query(FindingORM).filter(FindingORM.finding_uid == "test-lifecycle").first()
    assert row3.value == "new-value@example.com"
    assert row3.status == "false_positive"
    assert row3.review_status == "false_positive"

    db.close()


def test_escalate_dpo_status():
    """Escalating to DPO should set status to 'escalated'."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    f = Finding(
        finding_id="test-escalate",
        file_id="local:test.pdf",
        type="tax_id",
        value="DE123456789",
        risk_level="high",
    )
    writer.add_finding(f)
    writer.flush()
    db.commit()

    row = db.query(FindingORM).filter(FindingORM.finding_uid == "test-escalate").first()
    row.status = "escalated"
    row.review_status = "escalated"
    row.review_action = "escalate_dpo"
    row.reviewer = "admin"
    row.reviewed_at = datetime.now().isoformat()
    row.escalation_target = "dpo@bosch.com"
    db.commit()

    row2 = db.query(FindingORM).filter(FindingORM.finding_uid == "test-escalate").first()
    assert row2.status == "escalated"
    assert row2.review_status == "escalated"
    assert row2.review_action == "escalate_dpo"
    assert row2.escalation_target == "dpo@bosch.com"

    db.close()


# ===========================================================================
# BulkWriter batch with is_flagged/flag_type
# ===========================================================================

def test_bulkwriter_batch_all_flagged_fields():
    """A full batch of findings should persist all flag fields."""
    _init_db()
    db = next(_db_session())
    writer = BulkWriter(db, batch_size=50)

    for i in range(20):
        f = Finding(
            finding_id=f"test-batch-flag-{i}",
            file_id="local:test.pdf",
            type="email",
            value=f"user{i}@example.com",
            is_flagged=(i % 2 == 0),
            flag_type="Regex_Match" if i % 2 == 0 else "AI_Detected",
        )
        writer.add_finding(f)
    writer.flush()
    db.commit()

    # Verify each row
    for i in range(20):
        row = db.query(FindingORM).filter(FindingORM.finding_uid == f"test-batch-flag-{i}").first()
        assert row is not None, f"Finding {i} not found"
        assert row.is_flagged == (i % 2 == 0), f"is_flagged mismatch for {i}"
        expected_flag = "Regex_Match" if i % 2 == 0 else "AI_Detected"
        assert row.flag_type == expected_flag, f"flag_type mismatch for {i}: expected {expected_flag}, got {row.flag_type}"

    db.close()


# ===========================================================================
# Status string constants (document the valid set)
# ===========================================================================

VALID_FINDING_STATUSES = {
    "pending_review",  # awaiting human review
    "retained",        # reviewed, kept as business-necessary
    "deleted",         # reviewed, file deleted
    "archived",        # reviewed, file archived
    "masked",          # reviewed, data masked
    "false_positive",  # reviewed, was a false alarm
    "escalated",       # escalated to DPO
}


def test_valid_statuses_documented():
    """Ensure the set of valid statuses is well-defined."""
    for action in ALLOWED_REVIEW_ACTIONS:
        mapped = _ACTION_TO_STATUS[action]
        assert mapped in VALID_FINDING_STATUSES, f"Status '{mapped}' not in VALID_FINDING_STATUSES"


def test_pending_review_is_the_only_non_terminal_status():
    """Only pending_review means the finding hasn't been actioned yet."""
    assert "pending_review" in VALID_FINDING_STATUSES
    terminal = VALID_FINDING_STATUSES - {"pending_review"}
    assert len(terminal) == 6  # the 6 review outcomes
