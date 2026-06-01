"""FastAPI bridge between the GDPR dashboard frontend and the scanning pipeline.

Endpoints:
    POST /api/scan               – Trigger a scan on a folder path
    GET  /api/findings           – Search / filter / paginate findings
    POST /api/findings/{id}/review – Submit a human review action

Run:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import tempfile
import shutil

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# ── Internal pipeline imports ────────────────────────────────────────────────
from src.connector import LocalSampleRepoConnector
from src.scanner import run_ai_scan, run_full_scan
from src.models import (
    Finding as FindingDC,
    ScanResult as ScanResultDC,
    ScanMetrics as ScanMetricsDC,
    FileRef,
    ALLOWED_REVIEW_ACTIONS,
)

# ── Database imports ─────────────────────────────────────────────────────────
from database import (
    get_db,
    Finding as FindingORM,
    FileMetadata as FileMetadataORM,
    ScanJob as ScanJobORM,
    ScanError as ScanErrorORM,
    SessionLocal,
)

# ── Bulk DB writer ───────────────────────────────────────────────────────────
from src.db_writer import BulkWriter

# ── Optional: streaming scanner ──────────────────────────────────────────────
try:
    from src.streaming_scanner import run_streaming_scan as _streaming_scan

    _streaming_available = True
except Exception:
    _streaming_scan = None
    _streaming_available = False

# ── Optional: AI parser (graceful fallback to regex-only) ────────────────────
try:
    from src.ai_parser import AIParser

    _ai_parser: AIParser | None = AIParser()
except Exception:
    _ai_parser = None

# ═══════════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic request / response schemas  (strict, documented)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Scan ─────────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    """Payload for POST /api/scan."""
    folder_path: str = Field(
        ...,
        description="Absolute or relative path to the directory containing documents to scan.",
        examples=["/data/corporate_docs", "./sample_docs"],
    )
    mode: str = Field(
        default="delta",
        description="Scan mode: 'delta' or 'full'.",
        examples=["delta", "full"],
    )
    ai_mode: str = Field(
        default="layered",
        description="AI mode: 'off', 'layered', or 'full'.",
        examples=["off", "layered", "full"],
    )
    strict_hash: bool = Field(
        default=False,
        description="If True, compute content hash for changed candidates.",
    )

class ConnectRequest(BaseModel):
    """Payload for POST /api/connect."""
    source_type: str = Field(
        ...,
        description="Source type: local, googledrive, onedrive, sharepoint",
    )
    connection_config: dict = Field(
        default_factory=dict,
        description="Configuration object for the selected connector",
    )

class ConnectSummary(BaseModel):
    status: str = "success"
    source_type: str
    message: str


class ScanSummary(BaseModel):
    """Returned after a successful scan."""
    status: str = "success"
    scan_id: str
    timestamp: str
    files_scanned: int
    findings_count: int
    ai_enabled: bool


class ScanJobOut(BaseModel):
    """Returned by GET /api/scan/{scan_id}."""
    scan_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_files: int = 0
    files_scanned: int = 0
    files_skipped: int = 0
    files_error: int = 0
    total_findings: int = 0
    error_message: Optional[str] = None


class ScanJobStats(BaseModel):
    """Returned by GET /api/scan/{scan_id}/stats."""
    scan_id: str
    status: str
    discovery_time_ms: float = 0.0
    delta_time_ms: float = 0.0
    parse_time_ms: float = 0.0
    regex_time_ms: float = 0.0
    db_write_time_ms: float = 0.0
    ai_time_ms: float = 0.0
    total_time_ms: float = 0.0
    files_per_second: float = 0.0
    skip_ratio: float = 0.0
    peak_memory_mb: float = 0.0


class ScanErrorItem(BaseModel):
    """Single scan-level error."""
    file_id: str
    file_name: str
    error_type: str
    message: str


class ScanErrorsResponse(BaseModel):
    """Returned by GET /api/scan/{scan_id}/errors."""
    scan_id: str
    total_errors: int
    errors: list[ScanErrorItem]


class ScanJobsListItem(BaseModel):
    """Single item in scan job listing."""
    scan_id: str
    status: str
    created_at: str
    files_scanned: int = 0


class ScanJobsListResponse(BaseModel):
    """Returned by GET /api/scans."""
    scans: list[ScanJobsListItem]


# ── Findings ─────────────────────────────────────────────────────────────────

class FindingOut(BaseModel):
    """Single finding exposed to the frontend."""
    finding_id: str
    file_id: str
    type: str
    value: str
    field: str = ""
    context: str = "unknown"
    risk_level: str = "medium"
    confidence: float = 1.0
    evidence: str = ""
    recommended_action: str = "review"
    assigned_owner: str = ""
    owner_email: str = ""
    owner_department: str = ""
    owner_resolved: bool = False
    escalation_target: str = ""
    is_flagged: bool = True
    flag_type: str = ""
    # Review state (added by the API layer)
    review_status: str = "pending_review"
    review_action: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None


class RiskBreakdown(BaseModel):
    """Risk-level counts for dashboard charting."""
    high: int = 0
    medium: int = 0
    low: int = 0


class FindingsMetadata(BaseModel):
    """Aggregation metadata returned alongside paginated results."""
    total_count: int
    risk_breakdown: RiskBreakdown
    types_breakdown: dict[str, int] = {}


class FindingsResponse(BaseModel):
    """Full response for GET /api/findings."""
    results: list[FindingOut]
    metadata: FindingsMetadata


# ── Review ───────────────────────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    """Payload for POST /api/findings/{finding_id}/review."""
    action: str = Field(
        ...,
        description=f"The review action to apply. Allowed: {ALLOWED_REVIEW_ACTIONS}",
        examples=["delete", "mask", "retain"],
    )
    reviewer: str = Field(
        ...,
        description="Identifier of the person performing the review.",
        examples=["Admin", "dpo@bosch.com"],
    )
    reason: str = Field(
        default="",
        description="Optional justification for the action.",
    )


class ReviewResponse(BaseModel):
    """Confirmation returned after a review action."""
    status: str = "success"
    finding_id: str
    action: str
    reviewer: str
    reviewed_at: str

# ═══════════════════════════════════════════════════════════════════════════════
# Background scan runner
# ═══════════════════════════════════════════════════════════════════════════════


def _run_background_scan(
    scan_id: str,
    folder_path: str,
    mode: str,
    ai_mode: str,
    strict_hash: bool,
) -> None:
    """Execute a scan in a background thread and update the ScanJob record."""
    db = SessionLocal()
    t_start = time.perf_counter()

    try:
        # ── Update job to running ──────────────────────────────────────
        job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
        if not job:
            logger.error("ScanJob %s not found in DB", scan_id)
            return
        job.status = "running"
        job.started_at = datetime.now().isoformat()
        db.commit()

        # ── Set up connector and bulk writer ────────────────────────────
        # This path always scans the local folder it was given. External
        # sources (Google Drive / SharePoint / OneDrive) are ingested via
        # _run_ingest_job in main.py, not here — so we no longer branch on
        # the active connection (doing so ignored folder_path).
        connector = LocalSampleRepoConnector(repo_path=str(folder_path))

        writer = BulkWriter(db, batch_size=500)

        # ── Persist FileMetadata via BulkWriter (no duplicate list_files call) ──
        files = connector.list_files()
        job.total_files = len(files)
        db.commit()

        file_refs = []
        for file_meta in files:
            fr = FileRef(
                file_id=file_meta.file_id,
                file_name=file_meta.file_name,
                path_or_uri=file_meta.path,
                source_type="local",
                size_bytes=file_meta.size_bytes,
                last_modified=file_meta.last_modified,
                etag_or_version="",
            )
            writer.add_file_state(fr, content_hash=file_meta.content_hash)
            file_refs.append(fr)

        # ── Run the scanner ──────────────────────────────────────────────
        from src.streaming_scanner import run_streaming_scan
        from src.models import ScanOptions, FileScanResult
        
        def handle_result(r: FileScanResult):
            for f in r.findings:
                # extract_entities() leaves file_id empty ("set by scanner") —
                # stamp it here so the finding can be linked to its file row.
                if not f.file_id:
                    f.file_id = r.file_ref.file_id
                writer.add_finding(f)

        metrics = run_streaming_scan(
            connector,
            iter(file_refs),
            ScanOptions(mode=mode, max_workers=os.cpu_count() or 4),
            on_result=handle_result
        )

        # ── Flush remaining records ──────────────────────────────────────
        rows = writer.flush()
        db.commit()

        total_time_ms = (time.perf_counter() - t_start) * 1000

        # ── Store metrics ────────────────────────────────────────────────
        metrics_dict = {
            "total_time_ms": total_time_ms,
            "db_write_time_ms": writer.total_write_time_ms,
            "parse_time_ms": getattr(metrics, "parse_time_ms", 0.0),
            "regex_time_ms": getattr(metrics, "regex_time_ms", 0.0),
        }
        files_per_second = metrics.files_scanned / (total_time_ms / 1000) if total_time_ms > 0 else 0

        # ── Update job as completed ──────────────────────────────────────
        job.status = "completed"
        job.completed_at = datetime.now().isoformat()
        job.total_files = metrics.files_scanned
        job.files_scanned = metrics.files_scanned
        job.files_skipped = getattr(metrics, "files_skipped", 0)
        job.files_error = getattr(metrics, "files_error", 0)
        job.total_findings = metrics.total_findings
        job.metrics_json = json.dumps(metrics_dict)
        db.commit()

        logger.info(
            "Background scan %s complete: %d files, %d findings in %.0f ms (DB write: %.0f ms, %d flushes, %d rows)",
            scan_id,
            metrics.files_scanned,
            metrics.total_findings,
            total_time_ms,
            writer.total_write_time_ms,
            writer.flush_count,
            writer.total_rows_written,
        )
        
        # Cache creation logic for the massive local scan
        if metrics.files_scanned > 50000:
            import shutil
            from database import engine
            try:
                engine.dispose() # Ensure all transactions are flushed
                shutil.copy2("bosch_gdpr.db", "bosch_gdpr.cache.db")
                logger.info("Successfully created bosch_gdpr.cache.db for instant future restoration.")
            except Exception as cache_err:
                logger.error(f"Failed to create cache db: {cache_err}")

    except Exception as exc:
        logger.exception("Background scan %s failed", scan_id)
        try:
            job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)
                job.completed_at = datetime.now().isoformat()
                db.commit()
        except Exception:
            logger.exception("Failed to update ScanJob status for %s", scan_id)
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: persist a pipeline Finding dataclass → SQLAlchemy row
# ═══════════════════════════════════════════════════════════════════════════════


def _persist_finding(db: Session, f: FindingDC) -> FindingORM:
    """Upsert a pipeline Finding dataclass into the SQLite database."""
    existing = db.query(FindingORM).filter(FindingORM.finding_uid == f.finding_id).first()
    if existing:
        # Update in place on re-scan
        existing.type = f.type
        existing.value = f.value
        existing.field = f.field
        existing.context = f.context
        existing.risk_level = f.risk_level
        existing.confidence = f.confidence
        existing.evidence = f.evidence
        existing.recommended_action = f.recommended_action
        existing.assigned_owner = f.assigned_owner
        existing.owner_email = f.owner_email
        existing.owner_department = f.owner_department
        existing.owner_resolved = f.owner_resolved
        existing.escalation_target = f.escalation_target
        existing.is_flagged = f.is_flagged
        existing.flag_type = f.flag_type
        # Also mirror into the legacy columns for compatibility
        existing.category = f.type
        existing.confidence_score = f.confidence
        existing.flagged_snippet = f.value
        existing.reasoning = f.context
        return existing

    # Get the actual file ID from the FileMetadata table
    from database import FileMetadata
    filename = f.file_id.replace("local:", "")
    file_meta = db.query(FileMetadata).filter(FileMetadata.file_path.like(f"%{filename}")).first()
    actual_file_id = file_meta.id if file_meta else None

    row = FindingORM(
        finding_uid=f.finding_id,
        file_id=actual_file_id,
        file_id_str=f.file_id,
        type=f.type,
        value=f.value,
        field=f.field,
        context=f.context,
        risk_level=f.risk_level,
        confidence=f.confidence,
        evidence=f.evidence,
        recommended_action=f.recommended_action,
        assigned_owner=f.assigned_owner,
        owner_email=f.owner_email,
        owner_department=f.owner_department,
        owner_resolved=f.owner_resolved,
        escalation_target=f.escalation_target,
        is_flagged=f.is_flagged,
        flag_type=f.flag_type,
        # Legacy columns (used by admin KPIs in main.py)
        category=f.type,
        confidence_score=f.confidence,
        flagged_snippet=f.value,
        reasoning=f.context,
        status="pending_review",
        review_status="pending_review",
    )
    db.add(row)
    return row

# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI application
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="GDPR Data Discovery API",
    description="Bridge between the React/Next.js dashboard and the Python scanning pipeline.",
    version="2.0.0",
)

# CORS – wide open for local hackathon dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════════════════════
# Connections
# ═══════════════════════════════════════════════════════════════════════════════
# NOTE: The live /api/connect, /api/disconnect and /api/connection_status
# handlers live in main.py (the app that is actually served). This module's
# `app` is never mounted, so duplicate handlers here were dead code and have
# been removed. _run_background_scan, ConnectRequest and ConnectSummary above
# are still imported by main.py.

# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/scan
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/scan", response_model=ScanSummary)
async def trigger_scan(req: ScanRequest, db: Session = Depends(get_db)):
    """Trigger a GDPR scan on a given folder (non-blocking).

    1. Validates the folder path exists and is readable.
    2. Creates a ScanJob record with status="pending".
    3. Returns scan_id immediately.
    4. Scan runs in a background thread.
    """

    # ── Validate folder ──────────────────────────────────────────────────────
    folder = Path(req.folder_path).resolve()
    if not folder.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Folder not found: {folder}",
        )
    if not folder.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Path is not a directory: {folder}",
        )

    # ── Create scan job ──────────────────────────────────────────────────────
    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()
    options = {
        "mode": req.mode,
        "ai_mode": req.ai_mode,
        "strict_hash": req.strict_hash,
    }
    job = ScanJobORM(
        scan_id=scan_id,
        status="pending",
        options_json=json.dumps(options),
        created_at=now,
    )
    db.add(job)
    db.commit()

    logger.info("Scan job created: %s for folder: %s (mode=%s, ai=%s)", scan_id, folder, req.mode, req.ai_mode)

    # ── Launch background scan ───────────────────────────────────────────────
    thread = threading.Thread(
        target=_run_background_scan,
        args=(scan_id, str(folder), req.mode, req.ai_mode, req.strict_hash),
        daemon=True,
    )
    thread.start()

    return ScanSummary(
        scan_id=scan_id,
        timestamp=now,
        files_scanned=0,
        findings_count=0,
        ai_enabled=_ai_parser is not None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/scan/{scan_id} — scan job status
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/scan/{scan_id}", response_model=ScanJobOut)
async def get_scan_status(scan_id: str, db: Session = Depends(get_db)):
    """Return the current status and stats for a scan job."""
    job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Scan job '{scan_id}' not found.")

    return ScanJobOut(
        scan_id=job.scan_id,
        status=job.status,
        created_at=job.created_at or "",
        started_at=job.started_at,
        completed_at=job.completed_at,
        total_files=job.total_files or 0,
        files_scanned=job.files_scanned or 0,
        files_skipped=job.files_skipped or 0,
        files_error=job.files_error or 0,
        total_findings=job.total_findings or 0,
        error_message=job.error_message,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/scan/{scan_id}/stats — detailed timing metrics
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/scan/{scan_id}/stats", response_model=ScanJobStats)
async def get_scan_stats(scan_id: str, db: Session = Depends(get_db)):
    """Return detailed timing metrics for a scan job."""
    job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Scan job '{scan_id}' not found.")

    # Parse stored metrics JSON
    metrics = {}
    try:
        metrics = json.loads(job.metrics_json or "{}")
    except (json.JSONDecodeError, TypeError):
        pass

    total_time_ms = metrics.get("total_time_ms", 0.0)
    files_scanned = job.files_scanned or 0

    return ScanJobStats(
        scan_id=job.scan_id,
        status=job.status,
        discovery_time_ms=metrics.get("discovery_time_ms", 0.0),
        delta_time_ms=metrics.get("delta_time_ms", 0.0),
        parse_time_ms=metrics.get("parse_time_ms", 0.0),
        regex_time_ms=metrics.get("regex_time_ms", 0.0),
        db_write_time_ms=metrics.get("db_write_time_ms", 0.0),
        ai_time_ms=metrics.get("ai_time_ms", 0.0),
        total_time_ms=total_time_ms,
        files_per_second=files_scanned / (total_time_ms / 1000) if total_time_ms > 0 else 0.0,
        skip_ratio=metrics.get("skip_ratio", 0.0),
        peak_memory_mb=metrics.get("peak_memory_mb", 0.0),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/scan/{scan_id}/errors — file-level errors
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/scan/{scan_id}/errors", response_model=ScanErrorsResponse)
async def get_scan_errors(
    scan_id: str,
    skip: int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=500, description="Page size."),
    db: Session = Depends(get_db),
):
    """Return paginated list of file-level errors for a scan."""
    job = db.query(ScanJobORM).filter(ScanJobORM.scan_id == scan_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Scan job '{scan_id}' not found.")

    query = db.query(ScanErrorORM).filter(ScanErrorORM.scan_id == scan_id)
    total = query.count()
    errors = query.offset(skip).limit(limit).all()

    return ScanErrorsResponse(
        scan_id=scan_id,
        total_errors=total,
        errors=[
            ScanErrorItem(
                file_id=e.file_id or "",
                file_name=e.file_name or "",
                error_type=e.error_type or "unknown",
                message=e.message or "",
            )
            for e in errors
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/scans — list recent scan jobs
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/scans", response_model=ScanJobsListResponse)
async def list_scans(
    limit: int = Query(default=20, ge=1, le=100, description="Number of recent scans to return."),
    db: Session = Depends(get_db),
):
    """List recent scan jobs, newest first."""
    jobs = (
        db.query(ScanJobORM)
        .order_by(ScanJobORM.id.desc())
        .limit(limit)
        .all()
    )

    return ScanJobsListResponse(
        scans=[
            ScanJobsListItem(
                scan_id=j.scan_id,
                status=j.status,
                created_at=j.created_at or "",
                files_scanned=j.files_scanned or 0,
            )
            for j in jobs
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/upload
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/upload", response_model=ScanSummary)
async def upload_files(file: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Upload multiple files (e.g., from a folder selection) to scan.
    
    1. Saves all uploaded files to a temporary directory.
    2. Runs the same scan pipeline as /api/scan.
    3. Cleans up the temporary directory.
    """

    logger.info("Upload scan triggered with %d files", len(file))

    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Save all uploaded files to the temp directory
        for f_obj in file:
            if not f_obj.filename:
                continue
            
            # Use original filename (flattening any paths if sent by webkitdirectory)
            safe_name = Path(f_obj.filename).name
            temp_path = Path(temp_dir) / safe_name
            
            with open(temp_path, "wb") as f:
                shutil.copyfileobj(f_obj.file, f)
                
        # Run pipeline on the temporary directory
        try:
            connector = LocalSampleRepoConnector(repo_path=temp_dir)
            result: ScanResultDC = run_ai_scan(connector, ai_parser=_ai_parser, db_session=db)
        except Exception as exc:
            logger.exception("Pipeline error during uploaded file scan")
            raise HTTPException(
                status_code=500,
                detail=f"Scan pipeline failed: {exc}",
            )

        # Persist to SQLite
        for f in result.findings:
            _persist_finding(db, f)
        db.commit()

        logger.info(
            "Upload Scan complete — %d files scanned, %d findings (AI %s)",
            result.files_scanned,
            len(result.findings),
            "on" if _ai_parser else "off",
        )

        return ScanSummary(
            scan_id=result.scan_id,
            timestamp=result.timestamp,
            files_scanned=result.files_scanned,
            findings_count=len(result.findings),
            ai_enabled=_ai_parser is not None,
        )
    finally:
        # Always clean up the temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/findings
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/findings", response_model=FindingsResponse)
async def list_findings(
    q: Optional[str] = Query(
        default=None,
        description="Full-text search across `value` and `context` fields.",
    ),
    risk_level: Optional[str] = Query(
        default=None,
        description="Filter by risk level: high | medium | low.",
    ),
    type: Optional[str] = Query(
        default=None,
        description="Filter by GDPR type (e.g. email, tax_id, iban).",
    ),
    review_status: Optional[str] = Query(
        default=None,
        description="Filter by review status: pending_review | retained | deleted | archived | masked | false_positive | escalated.",
    ),
    skip: int = Query(default=0, ge=0, description="Pagination offset."),
    limit: int = Query(default=50, ge=1, le=500, description="Page size."),
    db: Session = Depends(get_db),
):
    """Query findings with full-text search, filters, and pagination.

    Returns paginated results plus aggregate metadata (totals + risk breakdown)
    so the dashboard can render charts without extra API calls.
    """
    # ── Build SQLAlchemy query with filters ───────────────────────────────────
    query = db.query(FindingORM)

    if risk_level:
        query = query.filter(FindingORM.risk_level.ilike(risk_level))
    if type:
        query = query.filter(FindingORM.type.ilike(type))
    if review_status:
        query = query.filter(FindingORM.review_status.ilike(review_status))
    if q:
        q_pattern = f"%{q.strip()}%"
        query = query.filter(
            FindingORM.value.ilike(q_pattern) | FindingORM.context.ilike(q_pattern)
        )

    all_filtered = query.all()

    # ── Compute metadata on the *full* filtered set (before pagination) ──────
    total = len(all_filtered)

    risk_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    types_counts: dict[str, int] = {}

    for f in all_filtered:
        rl = (f.risk_level or "medium").lower()
        risk_counts[rl] = risk_counts.get(rl, 0) + 1
        ft = f.type or "other"
        types_counts[ft] = types_counts.get(ft, 0) + 1

    # ── Paginate ─────────────────────────────────────────────────────────────
    page = all_filtered[skip : skip + limit]

    results = [
        FindingOut(
            finding_id=row.finding_uid or str(row.id),
            file_id=row.file_id_str or str(row.file_id or ""),
            type=row.type or row.category or "",
            value=row.value or row.flagged_snippet or "",
            field=row.field or "",
            context=row.context or "unknown",
            risk_level=row.risk_level or "medium",
            confidence=row.confidence if row.confidence is not None else 1.0,
            evidence=row.evidence or "",
            recommended_action=row.recommended_action or "review",
            assigned_owner=row.assigned_owner or "",
            owner_email=row.owner_email or "",
            owner_department=row.owner_department or "",
            owner_resolved=row.owner_resolved or False,
            escalation_target=row.escalation_target or "",
            is_flagged=bool(row.is_flagged) if row.is_flagged is not None else True,
            flag_type=row.flag_type or "",
            review_status=row.review_status or "pending_review",
            review_action=row.review_action,
            reviewer=row.reviewer,
            reviewed_at=row.reviewed_at,
        )
        for row in page
    ]

    return FindingsResponse(
        results=results,
        metadata=FindingsMetadata(
            total_count=total,
            risk_breakdown=RiskBreakdown(**risk_counts),
            types_breakdown=types_counts,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/findings/{finding_id}/review
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/findings/{finding_id}/review", response_model=ReviewResponse)
async def review_finding(finding_id: str, req: ReviewRequest, db: Session = Depends(get_db)):
    """Record a human review action on a specific finding.

    Updates the finding's review state in the database so subsequent
    GET /api/findings queries reflect the decision.
    """
    # ── Validate action ──────────────────────────────────────────────────────
    if req.action not in ALLOWED_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid action '{req.action}'. "
                f"Allowed actions: {ALLOWED_REVIEW_ACTIONS}"
            ),
        )

    # ── Locate finding in DB ─────────────────────────────────────────────────
    finding = db.query(FindingORM).filter(FindingORM.finding_uid == finding_id).first()
    if finding is None:
        # Fallback: try by integer id
        try:
            finding = db.query(FindingORM).filter(FindingORM.id == int(finding_id)).first()
        except (ValueError, TypeError):
            pass
    if finding is None:
        raise HTTPException(
            status_code=404,
            detail=f"Finding '{finding_id}' not found.",
        )

    # ── Apply review ─────────────────────────────────────────────────────────
    now = datetime.now().isoformat()
    finding.review_action = req.action
    finding.reviewer = req.reviewer
    finding.reviewed_at = now
    finding.recommended_action = req.action

    # Map action to lifecycle status (shared by status + review_status)
    _action_status_map = {
        "retain": "retained",
        "delete": "deleted",
        "archive": "archived",
        "mask": "masked",
        "false_positive": "false_positive",
        "escalate_dpo": "escalated",
    }
    new_state = _action_status_map.get(req.action, req.action)
    finding.status = new_state
    finding.review_status = new_state

    # ── Handle actual file deletion / archival ───────────────────────────────
    if req.action == "delete":
        # 1) Resolve the physical file path
        file_path: Path | None = None

        # Try via the FileMetadata table (legacy int FK)
        if finding.file_id:
            file_record = db.query(FileMetadataORM).filter(
                FileMetadataORM.id == finding.file_id
            ).first()
            if file_record and file_record.file_path:
                file_path = Path(file_record.file_path).resolve()

        # Fallback: use the pipeline's string file id (often the real path)
        if file_path is None and finding.file_id_str:
            candidate = Path(finding.file_id_str)
            if candidate.exists():
                file_path = candidate.resolve()

        # 2) Move or delete the file
        archive_dir = Path("strict_drive/archive").resolve()
        deletion_note = ""

        if file_path and file_path.exists():
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / file_path.name
                # Avoid name collisions in archive
                if dest.exists():
                    stem = dest.stem
                    suffix = dest.suffix
                    counter = 1
                    while dest.exists():
                        dest = archive_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                shutil.move(str(file_path), str(dest))
                deletion_note = f"Archived to {dest}"
                logger.info("File archived: %s → %s", file_path, dest)
            except OSError as exc:
                logger.warning("Could not archive file %s: %s", file_path, exc)
                deletion_note = f"Archive failed: {exc}"
        elif file_path:
            deletion_note = f"File already removed from disk: {file_path}"
            logger.info("File already gone, marking as deleted: %s", file_path)
        else:
            deletion_note = "No physical file path associated with this finding"
            logger.info("No file path for finding %s — marking status only", finding_id)

        # 3) Update the finding + all sibling findings for the same file
        finding.owner_resolved = True

        # Resolve siblings (other findings pointing to the same file)
        sibling_filter = []
        if finding.file_id:
            sibling_filter.append(FindingORM.file_id == finding.file_id)
        if finding.file_id_str:
            sibling_filter.append(FindingORM.file_id_str == finding.file_id_str)

        if sibling_filter:
            from sqlalchemy import or_
            siblings = db.query(FindingORM).filter(
                or_(*sibling_filter),
                FindingORM.id != finding.id,
            ).all()
            for sib in siblings:
                sib.status = "deleted"
                sib.review_status = "deleted"
                sib.review_action = "delete"
                sib.reviewer = req.reviewer
                sib.reviewed_at = now
                sib.owner_resolved = True

        # 4) Mark the FileMetadata row if it exists
        if finding.file_id:
            file_rec = db.query(FileMetadataORM).filter(
                FileMetadataORM.id == finding.file_id
            ).first()
            if file_rec:
                file_rec.file_path = f"[ARCHIVED] {file_rec.file_path}"

        logger.info("Delete action complete for finding %s: %s", finding_id, deletion_note)

    db.commit()

    logger.info(
        "Finding %s reviewed: action=%s by=%s",
        finding_id,
        req.action,
        req.reviewer,
    )

    return ReviewResponse(
        finding_id=finding_id,
        action=req.action,
        reviewer=req.reviewer,
        reviewed_at=now,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health(db: Session = Depends(get_db)):
    """Quick health probe for docker / monitoring."""
    return {
        "status": "ok",
        "findings_loaded": db.query(FindingORM).count(),
        "ai_enabled": _ai_parser is not None,
    }
