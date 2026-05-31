"""FastAPI bridge between the GDPR dashboard frontend and the scanning pipeline.

Endpoints:
    POST /api/scan               – Trigger a scan on a folder path
    GET  /api/findings           – Search / filter / paginate findings
    POST /api/findings/{id}/review – Submit a human review action

Run:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import logging
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
from src.scanner import run_ai_scan
from src.models import (
    Finding as FindingDC,
    ScanResult as ScanResultDC,
    ALLOWED_REVIEW_ACTIONS,
)

# ── Database imports ─────────────────────────────────────────────────────────
from database import get_db, Finding as FindingORM, FileMetadata as FileMetadataORM

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


class ScanSummary(BaseModel):
    """Returned after a successful scan."""
    status: str = "success"
    scan_id: str
    timestamp: str
    files_scanned: int
    findings_count: int
    ai_enabled: bool


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
    # Review state (added by the API layer)
    review_status: str = "pending"
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
        # Also mirror into the legacy columns for compatibility
        existing.category = f.type
        existing.confidence_score = f.confidence
        existing.flagged_snippet = f.value
        existing.reasoning = f.context
        return existing

    row = FindingORM(
        finding_uid=f.finding_id,
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
        # Legacy columns (used by admin KPIs in main.py)
        category=f.type,
        confidence_score=f.confidence,
        flagged_snippet=f.value,
        reasoning=f.context,
        status="Pending",
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
# POST /api/scan
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/scan", response_model=ScanSummary)
async def trigger_scan(req: ScanRequest, db: Session = Depends(get_db)):
    """Run the full GDPR scan pipeline on a given folder.

    1. Validates the folder path exists and is readable.
    2. Initialises LocalSampleRepoConnector with the provided path.
    3. Executes run_ai_scan (falls back to regex-only if no API key).
    4. Persists all findings to the SQLite database.
    5. Returns a concise summary for the UI.
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

    logger.info("Scan triggered for folder: %s", folder)

    # ── Run pipeline ─────────────────────────────────────────────────────────
    try:
        connector = LocalSampleRepoConnector(repo_path=str(folder))
        result: ScanResultDC = run_ai_scan(connector, ai_parser=_ai_parser, db_session=db)
    except Exception as exc:
        logger.exception("Pipeline error during scan")
        raise HTTPException(
            status_code=500,
            detail=f"Scan pipeline failed: {exc}",
        )

    # ── Persist to SQLite ────────────────────────────────────────────────────
    for f in result.findings:
        _persist_finding(db, f)
    db.commit()

    logger.info(
        "Scan complete — %d files, %d findings (AI %s)",
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
        description="Filter by review status: pending | completed.",
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
            review_status=row.review_status or "pending",
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
    finding.review_status = "completed"
    finding.review_action = req.action
    finding.reviewer = req.reviewer
    finding.reviewed_at = now
    finding.recommended_action = req.action

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
        archive_dir = Path("mock_drive/archive").resolve()
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
        finding.status = "Completed - Deleted"
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
                sib.status = "Completed - Deleted"
                sib.review_status = "completed"
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
    else:
        # Non-delete actions — just update the legacy status column
        finding.status = req.action.capitalize()

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
