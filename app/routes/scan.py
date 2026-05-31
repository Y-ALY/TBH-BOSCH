"""Scan-related API routes extracted from main.py.

Contains:
- POST /api/admin/trigger-scan     -- delta-aware full scan
- POST /api/admin/trigger-extraction -- memory-safe extraction pipeline
- GET  /api/admin/extraction-results  -- cached extraction results
- POST /api/scan/image             -- OCR image upload scan
- GET  /api/scan/image/cache-stats -- OCR cache statistics
- POST /api/scan/image/clear-cache -- flush OCR cache
- POST /api/findings/{finding_id}/review -- human review action

Mounted in main.py so existing URLs do not change.
"""

import os
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query as QueryParam, Cookie
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel as PydanticBaseModel

from database import get_db, FileMetadata, Finding

# Default scan root for the demo. Override with SCAN_ROOT env var.
SCAN_ROOT = os.environ.get("SCAN_ROOT", "./demo_drive_rich")

router = APIRouter()

# ── Module-level cache for extraction results ─────────────────────────────────
# Caches the latest extraction result in-process to avoid re-scanning
# for dashboard refreshes.  Intentionally a module-level dict, not a DB
# table, so it resets on server restart.
_latest_extraction_result: dict = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════════════════════════════════════

class TriggerScanRequest(PydanticBaseModel):
    """Optional body for POST /api/admin/trigger-scan."""
    target_dir: str = SCAN_ROOT
    previous_scan_id: Optional[str] = None  # e.g. "scan-a1b2c3d4"


class TriggerExtractionRequest(PydanticBaseModel):
    """Body for POST /api/admin/trigger-extraction."""
    target_dir: str = SCAN_ROOT


_ALLOWED_REVIEW_ACTIONS = [
    "retain", "delete", "archive", "mask",
    "false_positive", "escalate_dpo",
]


class ReviewRequest(PydanticBaseModel):
    """Payload for POST /api/findings/{finding_id}/review."""
    action: str
    reviewer: str = "Admin"
    reason: str = ""


# Allowed image MIME types for the OCR endpoint
_ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "image/gif",
}


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/admin/trigger-scan
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/admin/trigger-scan")
def trigger_manual_scan(
    req: TriggerScanRequest = TriggerScanRequest(),
    db: Session = Depends(get_db),
):
    """Run a delta-aware scan on a target directory.

    Flow:
      1. Look up a previous delta state file (by scan_id or 'latest').
      2. Run a delta comparison to categorise files as Added / Modified / Unchanged.
      3. Execute the full AI scan pipeline on the target directory.
      4. Filter findings to only those belonging to Added or Modified files.
      5. Persist new findings to the SQLite database.
      6. Save a new delta state snapshot for the next invocation.
      7. Return a structured response with file categories + new findings.
    """
    from pathlib import Path as _Path
    from src.connector import LocalSampleRepoConnector
    from src.scanner import run_ai_scan
    from src.delta import compare_delta, save_state

    target_dir = req.target_dir
    if not _Path(target_dir).exists():
        raise HTTPException(status_code=400, detail=f"Directory not found: {target_dir}")

    connector = LocalSampleRepoConnector(repo_path=target_dir)

    # ── Optional: AI parser (graceful fallback) ──────────────────────────
    try:
        from src.ai_parser import AIParser
        ai_parser = AIParser()
    except Exception:
        ai_parser = None

    # ── Delta comparison ─────────────────────────────────────────────────
    state_dir = _Path("data/state")
    state_dir.mkdir(parents=True, exist_ok=True)

    delta_report = None
    added_ids: set = set()
    modified_ids: set = set()
    removed_ids: list = []
    unchanged_count = 0

    # Resolve previous state path
    prev_state_path = None
    if req.previous_scan_id:
        candidate = state_dir / f"delta_state_{req.previous_scan_id}.json"
        if candidate.exists():
            prev_state_path = str(candidate)
    if prev_state_path is None:
        latest = state_dir / "latest.json"
        if latest.exists():
            prev_state_path = str(latest)

    if prev_state_path:
        try:
            delta_report = compare_delta(connector, prev_state_path)
            added_ids = {f.file_id for f in delta_report.added}
            modified_ids = {f.file_id for f in delta_report.modified}
            removed_ids = delta_report.removed
            unchanged_count = delta_report.unchanged
        except Exception as exc:
            # If delta fails, fall through to full scan
            delta_report = None

    # ── Run the full pipeline ────────────────────────────────────────────
    scan_result = run_ai_scan(connector, ai_parser=ai_parser, db_session=db)

    # ── Save new delta state ─────────────────────────────────────────────
    save_state(scan_result, str(state_dir))

    # ── Determine which findings are "new" ───────────────────────────────
    db_findings_count = db.query(Finding).count()
    if delta_report is not None and db_findings_count > 0:
        changed_ids = added_ids | modified_ids
        new_findings = [f for f in scan_result.findings if f.file_id in changed_ids]
    else:
        # First scan ever or DB wiped — everything is new
        new_findings = scan_result.findings
        added_ids = {f.file_id for f in connector.list_files()}

    # ── Persist FileMetadata for newly discovered files ──────────────────
    from datetime import datetime, timedelta
    for file_meta in connector.list_files():
        existing_fm = db.query(FileMetadata).filter(FileMetadata.file_path == file_meta.path).first()
        if not existing_fm:
            try:
                last_mod = datetime.fromisoformat(file_meta.last_modified)
            except:
                last_mod = datetime.now()
            new_fm = FileMetadata(
                file_path=file_meta.path,
                owner_employee_id="BX-17335", # default fallback if not caught by hints
                size_bytes=file_meta.size_bytes,
                file_hash=file_meta.content_hash,
                last_modified=last_mod,
                retention_deadline=datetime.now() + timedelta(days=200)
            )
            db.add(new_fm)

    # ── Clean up files missing from the filesystem ───────────────────────
    all_db_files = db.query(FileMetadata).all()
    for db_file in all_db_files:
        if not db_file.file_path.startswith("[DELETED]") and not os.path.exists(db_file.file_path):
            db.query(Finding).filter(Finding.file_id == db_file.id).delete()
            db.delete(db_file)
    db.commit()

    # ── Persist new findings to the DB (Optimized with Caching and Bulk Save) ─────────────────
    # 1. Cache all existing Finding UIDs to prevent duplicate checks
    existing_finding_uids = {f[0] for f in db.query(Finding.finding_uid).all() if f[0]}

    # 2. Cache all FileMetadata IDs by filename to prevent repeated file ID queries
    all_metas = db.query(FileMetadata.id, FileMetadata.file_path).all()
    meta_id_by_filename = {}
    for meta_id, file_path in all_metas:
        if file_path:
            filename = file_path.split("/")[-1].split("\\")[-1]
            meta_id_by_filename[filename] = meta_id

    new_finding_rows = []
    for f in new_findings:
        if f.finding_id in existing_finding_uids:
            continue  # skip duplicates

        filename = f.file_id.replace("local:", "")
        actual_file_id = meta_id_by_filename.get(filename)

        row = Finding(
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
            # Legacy columns
            category=f.type,
            confidence_score=f.confidence,
            flagged_snippet=f.value,
            reasoning=f.context,
            status="pending_review",
            review_status="pending_review",
        )
        new_finding_rows.append(row)

    if new_finding_rows:
        db.bulk_save_objects(new_finding_rows)
        db.commit()

    # ── Build categorised file lists for the response ────────────────────
    all_file_ids = {f.file_id for f in connector.list_files()}

    added_files = [
        {"file_id": fid, "status": "added"}
        for fid in sorted(added_ids)
    ]
    modified_files = [
        {"file_id": fid, "status": "modified"}
        for fid in sorted(modified_ids)
    ]
    unchanged_files_list = sorted(all_file_ids - added_ids - modified_ids)

    # ── Serialise only new findings for the response ─────────────────────
    findings_out = [
        {
            "finding_id": f.finding_id,
            "file_id": f.file_id,
            "type": f.type,
            "value": f.value,
            "risk_level": f.risk_level,
            "confidence": f.confidence,
            "assigned_owner": f.assigned_owner,
            "recommended_action": f.recommended_action,
        }
        for f in new_findings
    ]

    return {
        "status": "success",
        "scan_id": scan_result.scan_id,
        "timestamp": scan_result.timestamp,
        "is_delta": delta_report is not None,
        "files": {
            "added": added_files,
            "modified": modified_files,
            "unchanged": unchanged_files_list,
            "removed": removed_ids,
            "summary": {
                "added_count": len(added_files),
                "modified_count": len(modified_files),
                "unchanged_count": len(unchanged_files_list),
                "removed_count": len(removed_ids),
            },
        },
        "new_findings": findings_out,
        "new_findings_count": len(findings_out),
        "total_findings_in_scan": len(scan_result.findings),
        "ai_enabled": ai_parser is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/admin/trigger-extraction
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/admin/trigger-extraction")
def trigger_extraction(
    req: TriggerExtractionRequest = TriggerExtractionRequest(),
    db: Session = Depends(get_db),
):
    """Run the memory-safe extraction pipeline on a target directory.

    This endpoint:
      1. Loads owner_hints.json for file->owner mapping.
      2. Calls scan_directory() which processes files ONE AT A TIME
         using generator-based chunked reading (constant memory).
      3. Persists new findings to the SQLite database.
      4. Returns the strict JSON contract:
         - admin_aggregates: totals for the dashboard KPI blocks.
         - user_file_details: per-file owner + findings + context.

    The scan handles unlimited data -- a 5 TB drive uses the same
    RAM as a 5 MB folder because files are never fully loaded.
    """
    global _latest_extraction_result
    from pathlib import Path as _Path
    from src.extractor import scan_directory
    import json

    target_dir = req.target_dir
    if not _Path(target_dir).exists():
        raise HTTPException(status_code=400, detail=f"Directory not found: {target_dir}")

    # Load owner hints
    hints_path = _Path(target_dir) / "owner_hints.json"
    owner_hints = {}
    if hints_path.exists():
        with open(hints_path, "r", encoding="utf-8") as f:
            owner_hints = json.load(f)

    # ── Run the extraction pipeline (memory-safe, generator-based) ──
    result = scan_directory(str(target_dir), owner_hints=owner_hints)

    # ── Persist findings to the DB for the employee dashboard ──
    from datetime import datetime, timedelta
    import hashlib

    # Cache all existing finding values to prevent duplicates
    # Cache existing (file_id, finding_value) pairs to prevent duplicates within the same file
    existing_values = {
        (f.file_id, f.flagged_snippet) for f in db.query(Finding.file_id, Finding.flagged_snippet).all() if f.flagged_snippet
    }

    # Cache existing file names for FileMetadata lookup
    import database
    all_metas = db.query(database.FileMetadata.id, database.FileMetadata.file_path).all()
    meta_id_by_name = {os.path.basename(fp): mid for mid, fp in all_metas}

    new_findings_added = 0
    for file_detail in result.get("user_file_details", []):
        file_path = file_detail["file_path"]
        file_name = os.path.basename(file_path)
        file_id = meta_id_by_name.get(file_name)

        if not file_id:
            continue

        for finding in file_detail.get("findings", []):
            matched_val = finding["matched_value"]
            if (file_id, matched_val) in existing_values:
                continue
            existing_values.add((file_id, matched_val))

            row = Finding(
                file_id=file_id,
                category=finding["category"],
                confidence_score=finding["confidence"],
                flagged_snippet=matched_val,
                reasoning=finding["match_context"],
                status="pending_review",
                review_status="pending_review",
                # Extended fields
                type=finding["category"],
                value=matched_val,
                context=finding["match_context"],
                risk_level=finding["risk_level"],
                confidence=finding["confidence"],
                recommended_action=finding["recommended_action"],
                assigned_owner=file_detail.get("owner", ""),
                owner_email=file_detail.get("owner_email", ""),
                is_flagged=True,
                flag_type="Extractor_Regex",
            )
            db.add(row)
            new_findings_added += 1

    if new_findings_added:
        db.commit()

    # Cache result for the GET endpoint
    _latest_extraction_result = result

    # Add DB persistence stats to the response
    result["db_findings_added"] = new_findings_added

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/admin/extraction-results
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/admin/extraction-results")
def get_extraction_results():
    """Return the latest cached extraction results without re-scanning.

    Use this for dashboard refreshes -- it's instant because it reads
    from the in-process cache rather than re-scanning the filesystem.
    """
    if not _latest_extraction_result:
        return {
            "admin_aggregates": {
                "total_scanned_files": 0,
                "total_size_bytes": 0,
                "total_size_human": "0 B",
                "files_with_findings": 0,
                "total_findings": 0,
                "findings_by_category": {},
                "scan_duration_seconds": 0,
            },
            "user_file_details": [],
        }
    return _latest_extraction_result


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/scan/image
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/scan/image")
async def scan_uploaded_image(file: UploadFile = File(...)):
    """Upload an image file for OCR text extraction and PII compliance scanning.

    Accepts: PNG, JPEG, TIFF, BMP, WebP, GIF.

    Pipeline:
        1. Validate MIME type.
        2. Read file bytes (streamed).
        3. Delegate to ``src.ocr_scanner.scan_image()`` which handles:
           - SHA-256 content-addressable cache check
           - Tesseract OCR (on cache miss)
           - PII regex + semantic scanning via classifier.py
           - Immediate memory cleanup of raw bytes
        4. Return structured JSON response.

    Returns:
        {
            "status": "success",
            "cache_hit": true/false,
            "file_hash": "sha256hex...",
            "text": "...extracted text...",
            "flags": [ { "type": "...", "value": "...", ... } ]
        }
    """

    # ── MIME type validation ──────────────────────────────────────────────
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type: '{content_type}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_IMAGE_TYPES))}"
            ),
        )

    # ── Read file bytes ───────────────────────────────────────────────────
    try:
        file_bytes = await file.read()
    finally:
        await file.close()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # ── Delegate to OCR scanner ───────────────────────────────────────────
    try:
        from src.ocr_scanner import scan_image
        result = scan_image(file_bytes)
    except RuntimeError as exc:
        # Tesseract not installed
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(exc)}",
        )
    finally:
        # Drop the raw bytes reference to free memory immediately
        del file_bytes

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/scan/image/cache-stats
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/scan/image/cache-stats")
def ocr_cache_stats():
    """Return basic OCR cache statistics (admin/debug endpoint)."""
    from src.ocr_scanner import get_cache_stats
    return get_cache_stats()


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/scan/image/clear-cache
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/scan/image/clear-cache")
def ocr_clear_cache():
    """Flush the OCR result cache (admin endpoint)."""
    from src.ocr_scanner import clear_cache
    evicted = clear_cache()
    return {"status": "success", "evicted_entries": evicted}


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/findings/{finding_id}/review
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/findings/{finding_id}/review")
def review_finding(
    finding_id: str,
    req: ReviewRequest,
    db: Session = Depends(get_db),
):
    """Record a human review action on a finding."""
    from datetime import datetime as dt

    if req.action not in _ALLOWED_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action '{req.action}'. Allowed: {_ALLOWED_REVIEW_ACTIONS}",
        )

    # Locate finding by finding_uid or integer id
    finding = db.query(Finding).filter(Finding.finding_uid == finding_id).first()
    if finding is None:
        try:
            finding = db.query(Finding).filter(Finding.id == int(finding_id)).first()
        except (ValueError, TypeError):
            pass
    if finding is None:
        raise HTTPException(status_code=404, detail=f"Finding '{finding_id}' not found.")

    now = dt.now().isoformat()
    finding.review_action = req.action
    finding.reviewer = req.reviewer
    finding.reviewed_at = now
    finding.recommended_action = req.action

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
    finding.owner_resolved = True

    db.commit()

    return {
        "status": "success",
        "finding_id": finding_id,
        "action": req.action,
        "reviewer": req.reviewer,
        "reviewed_at": now,
    }
