from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta

# Using SQLite for instant, zero-config local development
SQLALCHEMY_DATABASE_URL = "sqlite:///./bosch_gdpr.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Add this class to database.py
class Employee(Base):
    __tablename__ = "employees"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    first_name = Column(String)
    last_name = Column(String)
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


# ── Reusable DB session dependency ────────────────────────────────────────────
def get_db():
    """Yield a SQLAlchemy session; auto-closes when the request ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Create the tables in the database
Base.metadata.create_all(bind=engine)