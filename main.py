
from fastapi import FastAPI, Depends, Request, Form, Response, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import database
from database import get_db, Employee, FileMetadata, Finding

import os
from typing import Optional, List, Dict, Any

app = FastAPI(title="Bosch GDPR Scan Engine API")

from database import SessionLocal

@app.on_event("startup")
def startup_event():
    import json
    import uuid
    from pathlib import Path
    from datetime import datetime, timedelta
    
    db = SessionLocal()
    try:
        print("Forcefully clearing DB tables for clean hackathon demo state...")
        db.query(Finding).delete()
        db.query(FileMetadata).delete()
        db.commit()
        
        # 1. Inject sample user data
        user = db.query(Employee).filter(Employee.email == "john.doe@company.com").first()
        if not user:
            user = Employee(
                employee_id="BX-1001",
                email="john.doe@company.com",
                first_name="John",
                last_name="Doe",
                password="password123",
                department="Engineering",
                location="Heilbronn"
            )
            db.add(user)
            db.commit()

        # 2. Open and parse the test cases JSON
        json_path = Path("tests/test_cases/workflow_input_test_cases.json")
        if not json_path.exists():
            print(f"\n[WARNING] Seed file missing: {json_path}")
            print("Please ensure the file exists. Mock data injection bypassed.\n")
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Defensively extract the list of cases
        cases = data.get("cases", []) if isinstance(data, dict) else data
        if not isinstance(cases, list):
            cases = []
            
        # Seed the DB with Finding models
        import re
        import hashlib
        for idx, case in enumerate(cases):
            doc = case.get("document", {})
            file_name = doc.get("file_name", "unknown_file.txt")
            content = doc.get("content", "")
            
            # Dynamically determine the owner_employee_id based on the email in the content
            email_match = re.search(r'[\w\.-]+@[\w\.-]+', content)
            email = email_match.group(0).lower().rstrip('.') if email_match else "john.doe@company.com"
            user_record = db.query(Employee).filter(Employee.email == email).first()
            if user_record:
                owner_id = user_record.employee_id
            else:
                emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
                owner_id = f"BX-{emp_id_int}"
            
            # Create FileMetadata to avoid breaking other endpoints
            f_meta = db.query(FileMetadata).filter(FileMetadata.file_path == f"/simulated_drive/{file_name}").first()
            if not f_meta:
                days_offset = -10 if idx % 3 == 0 else (15 if idx % 3 == 1 else 200)
                f_meta = FileMetadata(
                    file_path=f"/simulated_drive/{file_name}",
                    owner_employee_id=owner_id,
                    size_bytes=len(content),
                    file_hash=f"hash_{file_name}",
                    last_modified=datetime.now(),
                    retention_deadline=datetime.now() + timedelta(days=days_offset)
                )
                db.add(f_meta)
                db.commit()
                db.refresh(f_meta)
            
            expected = case.get("expected", {})
            categories = expected.get("challenge_personal_data_present", [])
            type_val = ", ".join(categories) if categories else "PII"
            
            # We can use part of the content as the flagged_snippet/value
            value_val = content[:100] + "..." if len(content) > 100 else content
            
            # Simple logic to determine risk
            risk_level = "medium"
            if "passport_number" in categories or "credit_card" in expected.get("exact_match_counts", {}):
                risk_level = "high"
                
            finding = Finding(
                finding_uid=str(uuid.uuid4()),
                file_id=f_meta.id,
                file_id_str=file_name,
                type=type_val,
                category=type_val,
                value=value_val,
                flagged_snippet=value_val,
                context=content,
                risk_level=risk_level,
                status="Pending",
                assigned_owner=owner_id # Good for UI logic
            )
            db.add(finding)
        
        # Explicitly create 3 'clean' FileMetadata records
        for i in range(1, 4):
            clean_meta = FileMetadata(
                file_path=f"/simulated_drive/clean_report_2026_{i}.pdf",
                owner_employee_id="BX-1001",
                size_bytes=5000,
                file_hash=f"hash_clean_{i}",
                last_modified=datetime.now(),
                retention_deadline=datetime.now() + timedelta(days=200)
            )
            db.add(clean_meta)
            
            clean_meta_anna = FileMetadata(
                file_path=f"/simulated_drive/anna_clean_report_2026_{i}.pdf",
                owner_employee_id="BX-26357",
                size_bytes=5000,
                file_hash=f"hash_anna_clean_{i}",
                last_modified=datetime.now(),
                retention_deadline=datetime.now() + timedelta(days=200)
            )
            db.add(clean_meta_anna)

        db.commit()
        print(f"Mock data successfully seeded from {json_path}!")
            
    except Exception as e:
        print(f"Error injecting mock data on startup: {e}")
    finally:
        db.close()
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(directory="templates")

# Allow the frontend to talk to this backend without security blocks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (good for hackathons)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],
)






def mask_sensitive_data(text: str) -> str:
    """Masks all but the first and last characters of a string."""
    if not text or len(text) <= 2:
        return "***"
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


@app.get("/")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={}
    )

from fastapi import Form
from fastapi.responses import RedirectResponse

@app.post("/login")
def login(
    request: Request, # <-- Added to allow template reloading
    response: Response,
    role: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if role == "admin":
        return RedirectResponse(url="/admin-dashboard", status_code=303)

    # Search for the user in the database
    user = db.query(Employee).filter(Employee.email == email).first()
    
    if not user or user.password != password:
        # THE FIX: Return the HTML page with an error flag instead of raw JSON
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid email or password. Please try again."}
        )

    # Redirect to dashboard AND set a browser cookie with their ID
    redirect = RedirectResponse(url="/employee-dashboard", status_code=303)
    redirect.set_cookie(key="session_emp_id", value=user.employee_id)
    return redirect


from typing import Optional

@app.get("/employee-dashboard")
def employee_dashboard(
    request: Request, 
    session_emp_id: Optional[str] = Cookie(None), # Grab the cookie
    db: Session = Depends(get_db)
):
    # If they have no cookie, kick them back to the login page
    if not session_emp_id:
        return RedirectResponse(url="/")
        
    # Get their specific data
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    
    # Pass the user data into the HTML template
    return templates.TemplateResponse(
        request=request,
        name="employee_dashboard.html",
        context={"user": user} # This is the magic link to the HTML
    )


@app.get("/employee-directory")
def employee_directory(
    request: Request,
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not session_emp_id:
        return RedirectResponse(url="/")
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    return templates.TemplateResponse(
        request=request,
        name="employee_directory.html",
        context={"user": user}
    )


@app.get("/data-points")
def data_points(
    request: Request,
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not session_emp_id:
        return RedirectResponse(url="/")
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    return templates.TemplateResponse(
        request=request,
        name="data_points.html",
        context={"user": user}
    )

@app.get("/user-details/{employee_id}")
def user_details(
    employee_id: str, 
    request: Request,
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not session_emp_id:
        return RedirectResponse(url="/")
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    return templates.TemplateResponse(
        request=request,
        name="user_details.html",
        context={
            "employee_id": employee_id,
            "user": user
        }
    )

from sqlalchemy import func

@app.get("/api/user-details/{employee_id}")
def get_user_details(employee_id: str, db: Session = Depends(get_db)):
    user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()
    file_results = []
    
    for file in user_files:
        file_findings = db.query(Finding).filter(Finding.file_id == file.id).all()
        findings_list = []
        
        for f in file_findings:
            findings_list.append({
                "finding_id": getattr(f, "finding_uid", None) or str(f.id),
                "category": getattr(f, "category", None) or getattr(f, "type", ""),
                "flagged_snippet": getattr(f, "flagged_snippet", None) or getattr(f, "value", ""),
                "reasoning": getattr(f, "reasoning", None) or getattr(f, "context", ""),
                "risk_level": getattr(f, "risk_level", "low"),
                "status": getattr(f, "status", "Pending"),
                "confidence_score": getattr(f, "confidence", 0.95)
            })
            
        # Defensively handle SQLite date strings vs objects
        ret_deadline = file.retention_deadline
        ret_deadline_str = None
        if ret_deadline:
            if isinstance(ret_deadline, str):
                ret_deadline_str = ret_deadline
            else:
                try:
                    ret_deadline_str = ret_deadline.isoformat()
                except Exception:
                    ret_deadline_str = str(ret_deadline)
        
        file_path = getattr(file, "file_path", "")
        file_name = file_path.split("/")[-1] if "/" in file_path else file_path.split("\\")[-1]
        
        file_results.append({
            "file_id": str(file.id),
            "file_name": file_name,
            "retention_deadline": ret_deadline_str,
            "findings": findings_list
        })

    return {
        "employee_id": employee_id,
        "files": file_results
    }



@app.get("/api/admin/kpis")
def get_admin_kpis(db: Session = Depends(get_db)):
    # 1. Total files scanned
    total_files = db.query(FileMetadata).count()
    
    # 2. Total data volume in bytes, converted to GB
    total_bytes = db.query(func.sum(FileMetadata.size_bytes)).scalar() or 0
    total_volume_gb = round(total_bytes / (1024 ** 3), 4) # Convert bytes to GB
    
    # 3. Total files with findings (using distinct to count each file only once)
    flagged_files = db.query(func.count(func.distinct(Finding.file_id))).scalar() or 0
    
    # 4. Get a summary of the most recent findings WITH MASKING applied
    recent_findings = db.query(Finding).order_by(Finding.id.desc()).limit(10).all()
    
    safe_findings = []
    for finding in recent_findings:
        safe_findings.append({
            "finding_id": finding.id,
            "category": finding.category,
            "confidence": finding.confidence_score,
            # MASKING IN ACTION: The admin sees the category, but not the actual data!
            "flagged_snippet_masked": mask_sensitive_data(finding.flagged_snippet),
            "status": finding.status
        })

    return {
        "metrics": {
            "total_scanned_files": total_files,
            "total_flagged_files": flagged_files,
            "total_volume_gb": total_volume_gb,
        },
        "recent_alerts": safe_findings
    }






from pydantic import BaseModel
from typing import List

# We create a simple data model for what the frontend expects
class ActionRequest(BaseModel):
    file_id: int
    action: str  # e.g., "delete" or "false_positive"

@app.get("/api/employee/files/{employee_id}")
def get_employee_files(employee_id: str, db: Session = Depends(get_db)):
    # 1. Find all files owned by this specific employee
    user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()
    user_file_ids = [f.id for f in user_files]
    
    if not user_file_ids:
        return {"message": "No files found for this user.", "findings": []}

    # 2. Find all active GDPR flags for those specific files
    user_findings = db.query(Finding).filter(
        Finding.file_id.in_(user_file_ids),
        Finding.status == "Pending"  # Only show unresolved issues
    ).all()
    
    # 3. Format the response for the frontend
    results = []
    for finding in user_findings:
        # We need the file name to show the user which file has the issue
        file_record = db.query(FileMetadata).filter(FileMetadata.id == finding.file_id).first()
        
        results.append({
            "finding_id": finding.id,
            "file_name": file_record.file_path.split("\\")[-1] if "\\" in file_record.file_path else file_record.file_path.split("/")[-1],
            "category": finding.category,
            "flagged_snippet": finding.flagged_snippet, # UNMASKED: The employee is allowed to see their own data
            "reasoning": finding.reasoning,
            "urgency": "Action Required" # You can tie this to the retention_deadline later
        })
        
    return {"findings": results}







@app.post("/api/employee/action")
def process_employee_action(request: ActionRequest, db: Session = Depends(get_db)):
    # This will handle the button clicks from the frontend
    finding = db.query(Finding).filter(Finding.id == request.file_id).first()
    if finding:
        finding.status = "Completed - Deleted"
        finding.owner_resolved = True
        db.commit()
    return {"status": "success", "message": f"Processed {request.action} on file {request.file_id}"}


from pydantic import BaseModel as PydanticBaseModel

class TriggerScanRequest(PydanticBaseModel):
    """Optional body for POST /api/admin/trigger-scan."""
    target_dir: str = "./mock_drive"
    previous_scan_id: Optional[str] = None  # e.g. "scan-a1b2c3d4"


@app.post("/api/admin/trigger-scan")
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
        from fastapi import HTTPException
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
    if delta_report is not None:
        changed_ids = added_ids | modified_ids
        new_findings = [f for f in scan_result.findings if f.file_id in changed_ids]
    else:
        # First scan ever — everything is new
        new_findings = scan_result.findings
        added_ids = {f.file_id for f in connector.list_files()}

    # ── Persist new findings to the DB ───────────────────────────────────
    for f in new_findings:
        existing = db.query(Finding).filter(Finding.finding_uid == f.finding_id).first()
        if existing:
            continue  # skip duplicates
        row = Finding(
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
            # Legacy columns
            category=f.type,
            confidence_score=f.confidence,
            flagged_snippet=f.value,
            reasoning=f.context,
            status="Pending",
        )
        db.add(row)
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






@app.post("/api/admin/seed-dummy-data")
def seed_dummy_data(db: Session = Depends(get_db)):
    """Here is where you plug in your new sample data!"""
    
    # 1. Plug your sample user data here
    user = db.query(Employee).filter(Employee.email == "john.doe@company.com").first()
    if not user:
        user = Employee(
            employee_id="BX-1001",
            email="john.doe@company.com",
            first_name="John",
            last_name="Doe",
            password="password123",
            department="Engineering",
            location="Heilbronn"
        )
        db.add(user)
        db.commit()

    # 2. Plug your sample files here and assign them to the user
    dummy_file = FileMetadata(
        file_path="/simulated_drive/john_doe_passport.pdf",
        owner_employee_id="BX-1001",
        size_bytes=1024500,
        file_hash="dummyhash123",
    )
    db.add(dummy_file)
    db.commit()

    # 3. Add the GDPR Finding
    dummy_finding = Finding(
        file_id=dummy_file.id,
        category="Passport Number",
        confidence_score=0.98,
        flagged_snippet="L9923481X",
        reasoning="Found standard passport format in onboarding doc.",
        status="Pending"
    )
    db.add(dummy_finding)
    db.commit()
    return {"message": "Dummy finding injected!"}


from fastapi import Query as QueryParam

@app.get("/api/search")
def search_findings(
    q: Optional[str] = QueryParam(None),
    risk_level: Optional[str] = QueryParam(None),
    type: Optional[str] = QueryParam(None),
    owner: Optional[str] = QueryParam(None),
    resolved: Optional[bool] = QueryParam(None),
    skip: int = QueryParam(0, ge=0),
    limit: int = QueryParam(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Search findings from the live SQLite database."""
    query = db.query(Finding)

    if risk_level:
        query = query.filter(Finding.risk_level == risk_level)
    if type:
        query = query.filter(Finding.type == type)
    if owner:
        query = query.filter(Finding.assigned_owner == owner)
    if resolved is not None:
        query = query.filter(Finding.owner_resolved == resolved)
    if q:
        q_pattern = f"%{q.lower()}%"
        query = query.filter(
            Finding.value.ilike(q_pattern)
            | Finding.context.ilike(q_pattern)
            | Finding.file_id_str.ilike(q_pattern)
        )

    all_results = query.all()
    total_count = len(all_results)

    risk_breakdown: Dict[str, int] = {}
    for res in all_results:
        rl = res.risk_level or "unknown"
        risk_breakdown[rl] = risk_breakdown.get(rl, 0) + 1

    paginated = all_results[skip : skip + limit]

    results = [
        {
            "finding_id": row.finding_uid or str(row.id),
            "file_id": row.file_id_str or str(row.file_id or ""),
            "type": row.type or row.category or "",
            "value": row.value or row.flagged_snippet or "",
            "context": row.context or "unknown",
            "risk_level": row.risk_level or "medium",
            "status": row.status or "Pending",
            "assigned_owner": row.assigned_owner or "",
            "owner_resolved": row.owner_resolved or False,
        }
        for row in paginated
    ]

    return {
        "results": results,
        "metadata": {
            "total_count": total_count,
            "risk_breakdown": risk_breakdown,
        },
    }

