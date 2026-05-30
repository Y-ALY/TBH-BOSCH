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
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import tempfile
import shutil

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── Internal pipeline imports ────────────────────────────────────────────────
from src.connector import LocalSampleRepoConnector
from src.scanner import run_ai_scan
from src.models import (
    Finding as FindingDC,
    ScanResult as ScanResultDC,
    ALLOWED_REVIEW_ACTIONS,
)

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
# In-memory data store  (thread-safe)
# ═══════════════════════════════════════════════════════════════════════════════

_lock = threading.Lock()

# Findings keyed by finding_id → dict (serialised from dataclass + review fields)
_findings_store: dict[str, dict] = {}

# Keep a reference to the last scan result for debugging / future use
_last_scan_result: ScanResultDC | None = None


def _finding_dc_to_dict(f: FindingDC) -> dict:
    """Convert a pipeline Finding dataclass into a dict with extra review fields."""
    d = asdict(f)
    d.update(
        review_status="pending",
        review_action=None,
        reviewer=None,
        reviewed_at=None,
    )
    return d

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
async def trigger_scan(req: ScanRequest):
    """Run the full GDPR scan pipeline on a given folder.

    1. Validates the folder path exists and is readable.
    2. Initialises LocalSampleRepoConnector with the provided path.
    3. Executes run_ai_scan (falls back to regex-only if no API key).
    4. Stores all findings in the in-memory store for subsequent queries.
    5. Returns a concise summary for the UI.
    """
    global _last_scan_result

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
        result: ScanResultDC = run_ai_scan(connector, ai_parser=_ai_parser)
    except Exception as exc:
        logger.exception("Pipeline error during scan")
        raise HTTPException(
            status_code=500,
            detail=f"Scan pipeline failed: {exc}",
        )

    # ── Persist to in-memory store ───────────────────────────────────────────
    with _lock:
        # Merge new findings (keyed by finding_id to allow re-scans)
        for f in result.findings:
            _findings_store[f.finding_id] = _finding_dc_to_dict(f)
        _last_scan_result = result

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
async def upload_files(file: List[UploadFile] = File(...)):
    """Upload multiple files (e.g., from a folder selection) to scan.
    
    1. Saves all uploaded files to a temporary directory.
    2. Runs the same scan pipeline as /api/scan.
    3. Cleans up the temporary directory.
    """
    global _last_scan_result

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
            result: ScanResultDC = run_ai_scan(connector, ai_parser=_ai_parser)
        except Exception as exc:
            logger.exception("Pipeline error during uploaded file scan")
            raise HTTPException(
                status_code=500,
                detail=f"Scan pipeline failed: {exc}",
            )

        # Persist to in-memory store
        with _lock:
            for f in result.findings:
                _findings_store[f.finding_id] = _finding_dc_to_dict(f)
            _last_scan_result = result

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
):
    """Query findings with full-text search, filters, and pagination.

    Returns paginated results plus aggregate metadata (totals + risk breakdown)
    so the dashboard can render charts without extra API calls.
    """
    with _lock:
        all_findings = list(_findings_store.values())

    # ── Apply filters ────────────────────────────────────────────────────────
    filtered: list[dict] = []
    q_lower = q.strip().lower() if q else None

    for f in all_findings:
        # Risk-level filter (case-insensitive)
        if risk_level and f.get("risk_level", "").lower() != risk_level.lower():
            continue

        # Type filter (case-insensitive)
        if type and f.get("type", "").lower() != type.lower():
            continue

        # Review-status filter
        if review_status and f.get("review_status", "").lower() != review_status.lower():
            continue

        # Full-text search on value + context
        if q_lower:
            val = f.get("value", "").lower()
            ctx = f.get("context", "").lower()
            if q_lower not in val and q_lower not in ctx:
                continue

        filtered.append(f)

    # ── Compute metadata on the *full* filtered set (before pagination) ──────
    total = len(filtered)

    risk_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    types_counts: dict[str, int] = {}

    for f in filtered:
        rl = f.get("risk_level", "medium").lower()
        risk_counts[rl] = risk_counts.get(rl, 0) + 1
        ft = f.get("type", "other")
        types_counts[ft] = types_counts.get(ft, 0) + 1

    # ── Paginate ─────────────────────────────────────────────────────────────
    page = filtered[skip : skip + limit]

    return FindingsResponse(
        results=[FindingOut(**f) for f in page],
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
async def review_finding(finding_id: str, req: ReviewRequest):
    """Record a human review action on a specific finding.

    Updates the finding's review state in the in-memory store so subsequent
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

    # ── Locate finding ───────────────────────────────────────────────────────
    with _lock:
        finding = _findings_store.get(finding_id)
        if finding is None:
            raise HTTPException(
                status_code=404,
                detail=f"Finding '{finding_id}' not found.",
            )

        # ── Apply review ─────────────────────────────────────────────────────
        now = datetime.now().isoformat()
        finding["review_status"] = "completed"
        finding["review_action"] = req.action
        finding["reviewer"] = req.reviewer
        finding["reviewed_at"] = now

        # If the action changes the recommended_action, mirror it
        finding["recommended_action"] = req.action

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
async def health():
    """Quick health probe for docker / monitoring."""
    return {
        "status": "ok",
        "findings_loaded": len(_findings_store),
        "ai_enabled": _ai_parser is not None,
    }
