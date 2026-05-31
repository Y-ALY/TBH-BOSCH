"""
Database layer — SQLAlchemy ORM models, engine/session setup, and reusable
dependencies for the TBH-BOSCH GDPR scanner.

Currently SQLite-backed.  The engine reads DATABASE_URL from the environment
so switching to PostgreSQL (or any SQLAlchemy-supported backend) requires
only a config change — no code edits.

SQLite Limitations (for operators coming from PostgreSQL):
  - Single-writer: only ONE writer at a time.  Concurrent writes from
    multiple workers / threads will hit SQLITE_BUSY and may be queued or
    dropped.  WAL mode (PRAGMA journal_mode=WAL) allows concurrent readers
    during a write but does NOT allow multiple simultaneous writers.
  - No ALTER TABLE: column/constraint changes that are a one-liner in
    PostgreSQL require a full table rebuild in SQLite (create-new-table,
    copy-data, drop-old, rename-new).  Alembic batch mode can paper over
    this during development, but production schema changes are fragile.
  - No row-level locking, no replication, no point-in-time recovery.
  - Connection pooling: the ``check_same_thread=False`` workaround used
    here is NOT safe for multi-process deployments (gunicorn workers,
    uvicorn with ``--workers > 1``).  Each process must open its own
    file-level connection.
  - File-based persistence: the .db file lives on the local filesystem.
    It is lost if the container/pod restarts without a persistent volume.

  Recommendation: migrate to PostgreSQL + Alembic before deploying to any
  shared or production environment (see Agent 9 / Phase 2).
"""

import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta


# =============================================================================
# Section 1 — Engine / Session Setup
# =============================================================================
# Environment-driven: set DATABASE_URL to point at PostgreSQL, SQLite is the
# local-dev / demo fallback.  Render sets DATABASE_URL automatically when a
# PostgreSQL addon is attached.

SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./bosch_gdpr.db")

_is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# Render free-tier PostgreSQL has a low connection limit (~5). NullPool avoids
# holding idle connections that would exhaust the limit.
_poolclass = None
if not _is_sqlite:
    from sqlalchemy.pool import NullPool
    _poolclass = NullPool

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=_connect_args,
    poolclass=_poolclass,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# =============================================================================
# Section 2 — ORM Models
# =============================================================================
# Each class maps to a SQLite (or PostgreSQL) table via SQLAlchemy's
# declarative base.  Tables are auto-created at import time by the
# ``Base.metadata.create_all(bind=engine)`` call at the bottom of this file.
#
# Naming note: ``FileMetadata`` here is a SQLAlchemy ORM class.  The
# scan-engine dataclass in ``src/models.py`` is also called ``FileMetadata``.
# Importers typically alias one to avoid confusion, e.g.
#     from database import FileMetadata as FileMetadataORM

class Employee(Base):
    __tablename__ = "employees"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    first_name = Column(String)
    last_name = Column(String)
    # ⚠️ DEMO-ONLY: Plaintext password storage. Not production-safe.
    # For production: hash with bcrypt/passlib, never store raw passwords.
    password = Column(String) # Plaintext is fine for a hackathon!
    department = Column(String)
    location = Column(String)

class FileMetadata(Base):
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, unique=True, index=True)
    owner_employee_id = Column(String, index=True)
    size_bytes = Column(Integer)
    last_modified = Column(DateTime)
    file_hash = Column(String) # Crucial for the Delta Scan
    retention_deadline = Column(DateTime)

class Finding(Base):
    __tablename__ = "findings"
    
    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, index=True)
    category = Column(String) # e.g., "Passport Number"
    confidence_score = Column(Float)
    flagged_snippet = Column(String)
    reasoning = Column(String)
    status = Column(String, default="pending_review")  # lifecycle: pending_review | retained | deleted | archived | masked | false_positive | escalated

    # ── Extended fields from the scan pipeline ─────────────────────────
    finding_uid = Column(String, unique=True, index=True)  # pipeline finding_id (str)
    file_id_str = Column(String, index=True)               # pipeline file_id (str path / name)
    type = Column(String)                                   # email | tax_id | iban …
    value = Column(String)                                  # the raw PII value found
    field = Column(String, default="")
    context = Column(String, default="unknown")
    risk_level = Column(String, default="medium")           # high | medium | low
    confidence = Column(Float, default=1.0)
    evidence = Column(String, default="")
    recommended_action = Column(String, default="review")
    assigned_owner = Column(String, default="")
    owner_email = Column(String, default="")
    owner_department = Column(String, default="")
    owner_resolved = Column(Boolean, default=False)
    escalation_target = Column(String, default="")
    is_flagged = Column(Boolean, default=True)
    flag_type = Column(String, default="")

    # Review state (managed by the API layer)
    review_status = Column(String, default="pending_review")  # mirrors status above; pending_review | retained | deleted | archived | masked | false_positive | escalated
    review_action = Column(String, nullable=True)
    reviewer = Column(String, nullable=True)
    reviewed_at = Column(String, nullable=True)


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(String, unique=True, index=True)
    status = Column(String, default="pending")  # pending|running|completed|failed|interrupted
    options_json = Column(String, default="{}")  # JSON-serialized ScanOptions
    metrics_json = Column(String, default="{}")  # JSON-serialized ScanMetrics
    created_at = Column(String)
    started_at = Column(String, nullable=True)
    completed_at = Column(String, nullable=True)
    total_files = Column(Integer, default=0)
    files_scanned = Column(Integer, default=0)
    files_skipped = Column(Integer, default=0)
    files_error = Column(Integer, default=0)
    total_findings = Column(Integer, default=0)
    error_message = Column(String, nullable=True)


class ScanError(Base):
    __tablename__ = "scan_errors"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(String, index=True)
    file_id = Column(String)
    file_name = Column(String)
    error_type = Column(String)
    message = Column(String)

class Notification(Base):
    """Admin → Employee deletion-request notifications."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String, index=True)          # target employee
    admin_id = Column(String)                          # who sent it
    message = Column(String, default="")
    file_ids = Column(String, default="[]")            # JSON list of file IDs
    status = Column(String, default="unread")          # unread | read | actioned
    created_at = Column(DateTime, default=datetime.now)
    actioned_at = Column(DateTime, nullable=True)


# =============================================================================
# Section 3 — Repository / Helper Functions
# =============================================================================
# Currently a single FastAPI dependency.  As the codebase grows, repository
# classes for Finding, FileMetadata, ScanJob, etc. should live here or in a
# dedicated ``data_access/repositories.py``.

def get_db():
    """Yield a SQLAlchemy session; auto-closes when the request ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Auto-create tables at import time (dev convenience) ─────────────────
# In production with PostgreSQL this should be replaced by Alembic migrations.
# ``create_all`` is safe for SQLite (creates only if not exists) but does NOT
# handle schema migrations (no ALTER TABLE — see module-level docstring).
Base.metadata.create_all(bind=engine)