
from fastapi import FastAPI, Depends, Request, Form, Response, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import database
from database import get_db, Employee, FileMetadata, Finding, Notification

import os
from typing import Optional, List, Dict, Any

app = FastAPI(title="Bosch GDPR Scan Engine API")

from database import SessionLocal

@app.on_event("startup")
def startup_event():
    import json
    import hashlib
    from pathlib import Path
    from datetime import datetime, timedelta
    from src.connector import LocalSampleRepoConnector
    
    db = SessionLocal()
    try:
        # We no longer clear the DB tables to preserve the delta state and findings across reboots.
        # This mitigates the issue of files appearing 'clean' initially.
        
        # 1. Load Owner Hints to generate Employees
        hints_path = Path("demo_drive_rich/owner_hints.json")
        hints = {}
        if hints_path.exists():
            with open(hints_path, "r", encoding="utf-8") as f:
                hints = json.load(f)
                
        # Always ensure admin exists
        admin = db.query(Employee).filter(Employee.email == "admin@bosch.com").first()
        if not admin:
            admin = Employee(
                employee_id="BX-ADMIN", email="admin@bosch.com",
                first_name="Admin", last_name="User",
                password="password123", department="IT Security", location="Stuttgart"
            )
            db.add(admin)
            db.commit()

        # Cache existing employees to completely avoid N+1 queries
        all_employees = db.query(Employee).all()
        added_emails = {e.email for e in all_employees}
        added_emp_ids = {e.employee_id for e in all_employees}
        email_to_emp_id = {e.email: e.employee_id for e in all_employees}

        new_employees = []
        for file_name, hint in hints.items():
            email = hint.get("email", "unknown@bosch.com")
            if email in added_emails:
                continue

            emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
            emp_id_str = f"BX-{emp_id_int}"
            while emp_id_str in added_emp_ids:
                emp_id_int = (emp_id_int - 10000 + 1) % 90000 + 10000
                emp_id_str = f"BX-{emp_id_int}"
            
            added_emp_ids.add(emp_id_str)
            added_emails.add(email)
            email_to_emp_id[email] = emp_id_str

            first, last = hint.get("name", "Unknown User").split(" ", 1) if " " in hint.get("name", "") else (hint.get("name", "User"), "")
            emp = Employee(
                employee_id=emp_id_str, email=email,
                first_name=first, last_name=last,
                password="password123", department=hint.get("department", "Unknown"), location="Unknown"
            )
            new_employees.append(emp)
            db.add(emp)
        
        if new_employees:
            db.commit()

        # 2. Ingest FileMetadata from demo_drive_rich
        connector = LocalSampleRepoConnector(repo_path="./demo_drive_rich")
        files = connector.list_files()
        
        # Cache existing file paths
        existing_file_paths = {f[0] for f in db.query(FileMetadata.file_path).all()}
        
        new_metas = []
        for idx, file_meta in enumerate(files):
            if file_meta.path in existing_file_paths:
                continue
                
            hint = hints.get(file_meta.file_name, {})
            email = hint.get("email")
            owner_id = email_to_emp_id.get(email, "BX-ADMIN") if email else "BX-ADMIN"
            
            days_offset = -10 if idx % 3 == 0 else (15 if idx % 3 == 1 else 200)
            
            try:
                last_mod = datetime.fromisoformat(file_meta.last_modified)
            except:
                last_mod = datetime.now()

            new_meta = FileMetadata(
                file_path=file_meta.path,
                owner_employee_id=owner_id,
                size_bytes=file_meta.size_bytes,
                file_hash=file_meta.content_hash,
                last_modified=last_mod,
                retention_deadline=datetime.now() + timedelta(days=days_offset)
            )
            new_metas.append(new_meta)
            db.add(new_meta)
            
        if new_metas:
            db.commit()
        
        # 3. Cleanup deleted files from the database to keep counts accurate
        import os
        all_db_files = db.query(FileMetadata).all()
        deleted_file_ids = []
        deleted_files = []
        for db_file in all_db_files:
            if not db_file.file_path.startswith("[DELETED]") and not os.path.exists(db_file.file_path):
                deleted_file_ids.append(db_file.id)
                deleted_files.append(db_file)
                
        if deleted_file_ids:
            db.query(Finding).filter(Finding.file_id.in_(deleted_file_ids)).delete(synchronize_session=False)
            for df in deleted_files:
                db.delete(df)
            db.commit()
        
        print(f"Successfully ingested {len(files)} files into FileMetadata on startup.")
            
    except Exception as e:
        print(f"Error injecting data on startup: {e}")
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
    request: Request,
    response: Response,
    role: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Search for the user in the database
    user = db.query(Employee).filter(Employee.email == email).first()
    
    if not user or user.password != password:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid email or password. Please try again."}
        )

    if role == "admin":
        redirect = RedirectResponse(url="/admin-dashboard", status_code=303)
    else:
        redirect = RedirectResponse(url="/employee-dashboard", status_code=303)
        
    redirect.set_cookie(key="session_emp_id", value=user.employee_id)
    return redirect


from typing import Optional

@app.get("/admin-dashboard")
def admin_dashboard(
    request: Request, 
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not session_emp_id:
        return RedirectResponse(url="/")
        
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={"user": user}
    )

@app.get("/admin-database-explorer")
def admin_database_explorer(
    request: Request, 
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not session_emp_id:
        return RedirectResponse(url="/")
        
    user = db.query(Employee).filter(Employee.employee_id == session_emp_id).first()
    
    return templates.TemplateResponse(
        request=request,
        name="admin_database_explorer.html",
        context={"user": user}
    )

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
                "status": getattr(f, "review_status", None) or getattr(f, "status", "pending_review"),
                "confidence_score": getattr(f, "confidence", 0.95)
            })
            
        # Defensively handle SQLite date strings vs objects
        ret_deadline = file.retention_deadline
        ret_deadline_str = None
        is_expired = False
        
        if ret_deadline:
            if isinstance(ret_deadline, str):
                ret_deadline_str = ret_deadline
                try:
                    from datetime import datetime
                    parsed_deadline = datetime.fromisoformat(ret_deadline)
                    if parsed_deadline.tzinfo is not None:
                        parsed_deadline = parsed_deadline.replace(tzinfo=None)
                    is_expired = parsed_deadline < datetime.now()
                except ValueError:
                    pass
            else:
                try:
                    ret_deadline_str = ret_deadline.isoformat()
                    from datetime import datetime
                    parsed_deadline = ret_deadline
                    if isinstance(parsed_deadline, datetime) and parsed_deadline.tzinfo is not None:
                        parsed_deadline = parsed_deadline.replace(tzinfo=None)
                    is_expired = parsed_deadline < datetime.now()
                except Exception:
                    ret_deadline_str = str(ret_deadline)
        
        file_path = getattr(file, "file_path", "")
        file_name = file_path.split("/")[-1] if "/" in file_path else file_path.split("\\")[-1]
        
        last_mod = getattr(file, "last_modified", None)
        last_mod_str = None
        if last_mod:
            if isinstance(last_mod, str):
                last_mod_str = last_mod
            else:
                try:
                    last_mod_str = last_mod.isoformat()
                except Exception:
                    last_mod_str = str(last_mod)
        
        file_results.append({
            "file_id": str(file.id),
            "file_name": file_name,
            "file_path": file_path,
            "size_bytes": getattr(file, "size_bytes", 0) or 0,
            "last_modified": last_mod_str,
            "retention_deadline": ret_deadline_str,
            "expired": is_expired,
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

@app.get("/api/admin/employees/search")
def search_employees(q: str, db: Session = Depends(get_db)):
    if not q:
        return []
    
    q_lower = f"%{q.lower()}%"
    from sqlalchemy import or_, func
    
    employees = db.query(Employee).filter(
        or_(
            func.lower(Employee.first_name).like(q_lower),
            func.lower(Employee.last_name).like(q_lower),
            func.lower(Employee.email).like(q_lower),
            func.lower(Employee.employee_id).like(q_lower),
            func.lower(Employee.first_name + " " + Employee.last_name).like(q_lower)
        )
    ).limit(50).all()
    
    results = []
    for emp in employees:
        results.append({
            "employee_id": emp.employee_id,
            "first_name": emp.first_name,
            "last_name": emp.last_name,
            "email": emp.email,
            "department": emp.department,
            "location": emp.location
        })
    return results

@app.post("/api/admin/extend-retention/{file_id}")
def extend_retention(file_id: str):
    # Mock updating the retention to indicate needed for a while
    return {"status": "success", "message": "Retention extended"}


from pydantic import BaseModel

class RetainDocumentRequest(BaseModel):
    reason: str
    project_name: str
    notes: str = ""
    admin_email: str = ""

@app.post("/api/admin/retain-document/{file_id}")
def retain_document(file_id: str, req: RetainDocumentRequest):
    """Mark a document as business-critical with a justification.
    
    The admin provides a reason (e.g., 'active_project'), a project name,
    and optional notes explaining why the flagged data must be retained
    beyond its scheduled GDPR deletion date.
    """
    import logging
    logging.info(
        "RETENTION JUSTIFICATION — file=%s reason=%s project=%s admin=%s notes=%s",
        file_id, req.reason, req.project_name, req.admin_email, req.notes
    )
    return {
        "status": "success",
        "message": f"Document '{file_id}' retained for business reasons: {req.project_name}",
        "file_id": file_id,
        "reason": req.reason,
        "project_name": req.project_name
    }






from pydantic import BaseModel
from typing import List

from typing import Union

# We create a simple data model for what the frontend expects
class ActionRequest(BaseModel):
    file_id: Union[int, str]
    action: str  # e.g., "delete" or "false_positive"

@app.get("/api/employee/files/{employee_id}")
def get_employee_files(employee_id: str, db: Session = Depends(get_db)):
    from datetime import datetime
    
    # 1. Find all files owned by this specific employee
    user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()
    user_file_ids = [f.id for f in user_files]
    
    if not user_file_ids:
        return {"message": "No files found for this user.", "findings": []}

    # 2. Find all active GDPR flags for those specific files
    user_findings = db.query(Finding).filter(
        Finding.file_id.in_(user_file_ids),
        Finding.review_status == "pending_review"  # Only show unresolved issues
    ).all()
    
    # 3. Format the response for the frontend
    results = []
    for finding in user_findings:
        # We need the file name to show the user which file has the issue
        file_record = db.query(FileMetadata).filter(FileMetadata.id == finding.file_id).first()
        
        # Calculate if file's retention deadline has expired
        retention_deadline = file_record.retention_deadline
        is_expired = False
        if retention_deadline:
            if isinstance(retention_deadline, str):
                try:
                    retention_deadline = datetime.fromisoformat(retention_deadline)
                except ValueError:
                    pass
            if isinstance(retention_deadline, datetime):
                if retention_deadline.tzinfo is not None:
                    retention_deadline = retention_deadline.replace(tzinfo=None)
                is_expired = retention_deadline < datetime.now()
        
        results.append({
            "finding_id": finding.id,
            "file_id": file_record.id,
            "file_name": file_record.file_path.split("\\")[-1] if "\\" in file_record.file_path else file_record.file_path.split("/")[-1],
            "category": finding.category,
            "flagged_snippet": finding.flagged_snippet, # UNMASKED: The employee is allowed to see their own data
            "reasoning": finding.reasoning,
            "expired": is_expired,
            "urgency": "IMMEDIATE DELETION REQUIRED" if is_expired else "Action Required"
        })
        
    return {"findings": results}







@app.post("/api/employee/action")
def process_employee_action(request: ActionRequest, db: Session = Depends(get_db)):
    import os
    # Query defensively by both auto-increment id and finding_uid (string UUID)
    finding = None
    if isinstance(request.file_id, int) or (isinstance(request.file_id, str) and request.file_id.isdigit()):
        finding = db.query(Finding).filter(Finding.id == int(request.file_id)).first()
    
    if not finding:
        finding = db.query(Finding).filter(Finding.finding_uid == str(request.file_id)).first()
        
    if finding:
        # Determine explicit finding status based on action
        action_mapping = {
            "false_positive": "false_positive",
            "keep": "kept",
            "delete": "deleted",
            "export": "kept",
            "explain": "needs_review",
            "resolved": "resolved"
        }
        
        new_status = action_mapping.get(request.action, "resolved")
        finding.status = new_status
        finding.review_status = new_status
        finding.owner_resolved = True
        
        # Explicitly handle distinction between metadata update vs physical deletion
        if request.action == "delete":
            # Physically delete the file from storage if requested
            if finding.file_id:
                file_meta = db.query(FileMetadata).filter(FileMetadata.id == finding.file_id).first()
                if file_meta and not file_meta.file_path.startswith("[DELETED]"):
                    file_path = file_meta.file_path
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        file_meta.file_path = f"[DELETED] {file_path}"
                    except Exception:
                        pass
        
        db.commit()
        return {"status": "success", "message": f"Processed {request.action} on finding {request.file_id}"}
        
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"Finding {request.file_id} not found")


class DeleteExpiredRequest(BaseModel):
    employee_id: str

@app.post("/api/employee/files/{file_id}/delete-expired")
def delete_expired_file(
    file_id: int,
    req: DeleteExpiredRequest,
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    import os
    from datetime import datetime
    
    # 1. Fetch file metadata
    file_meta = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_meta:
        raise HTTPException(status_code=404, detail="File Not Found in DB")
        
    # 2. Verify ownership
    if file_meta.owner_employee_id != req.employee_id:
        raise HTTPException(status_code=403, detail="Unauthorized Owner")
        
    # 3. Time Validation
    # Parse retention_deadline if it's a string (defensive check)
    retention_deadline = file_meta.retention_deadline
    if isinstance(retention_deadline, str):
        try:
            retention_deadline = datetime.fromisoformat(retention_deadline)
        except ValueError:
            # Fallback parsing
            pass
            
    if isinstance(retention_deadline, datetime) and retention_deadline.tzinfo is not None:
        retention_deadline = retention_deadline.replace(tzinfo=None)
            
    if not retention_deadline or retention_deadline > datetime.now():
        raise HTTPException(status_code=400, detail="File retention deadline has not expired yet")
        
    # 4. Physical Deletion
    file_path = file_meta.file_path
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except FileNotFoundError:
        pass # Gracefully handle if already manually removed
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OS Deletion Error: {str(e)}")
        
    # 5. Database Updates
    try:
        # Update associated Findings
        findings = db.query(Finding).filter(Finding.file_id == file_id).all()
        for finding in findings:
            finding.status = "deleted"
            finding.review_status = "deleted"
            finding.owner_resolved = True

        # Update FileMetadata
        file_meta.file_path = f"[DELETED] {file_path}"
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database Update Error: {str(e)}")
        
    return {"status": "success", "message": f"File {file_id} deleted successfully"}

class ExtendRetentionRequest(BaseModel):
    employee_id: str

@app.post("/api/employee/files/{file_id}/extend-retention")
def extend_retention(
    file_id: int,
    req: ExtendRetentionRequest,
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    from datetime import datetime, timedelta
    
    file_meta = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_meta:
        raise HTTPException(status_code=404, detail="File Not Found in DB")
        
    if file_meta.owner_employee_id != req.employee_id:
        raise HTTPException(status_code=403, detail="Unauthorized Owner")
        
    try:
        # Extend retention by 90 days from now
        file_meta.retention_deadline = datetime.now() + timedelta(days=90)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database Update Error: {str(e)}")
        
    return {"status": "success", "message": f"Retention extended successfully for file {file_id}"}

from pydantic import BaseModel as PydanticBaseModel

class TriggerScanRequest(PydanticBaseModel):
    """Optional body for POST /api/admin/trigger-scan."""
    target_dir: str = "./demo_drive_rich"
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
    import os
    all_db_files = db.query(FileMetadata).all()
    for db_file in all_db_files:
        if not db_file.file_path.startswith("[DELETED]") and not os.path.exists(db_file.file_path):
            db.query(Finding).filter(Finding.file_id == db_file.id).delete()
            db.delete(db_file)
    db.commit()

    # ── Persist new findings to the DB ───────────────────────────────────
    for f in new_findings:
        existing = db.query(Finding).filter(Finding.finding_uid == f.finding_id).first()
        if existing:
            continue  # skip duplicates
            
        # Get the actual file ID from the FileMetadata table
        filename = f.file_id.replace("local:", "")
        file_meta = db.query(FileMetadata).filter(FileMetadata.file_path.like(f"%{filename}")).first()
        actual_file_id = file_meta.id if file_meta else None
            
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
        status="pending_review",
        review_status="pending_review",
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
            "status": row.review_status or row.status or "pending_review",
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


# ── Notification & Compliance Score System ──────────────────────────────────

import json as _json
from datetime import datetime as _dt, timedelta as _td


class DeletionRequestPayload(PydanticBaseModel):
    target_employee_id: str
    file_ids: List[int]
    message: str = ""
    admin_employee_id: str


@app.post("/api/admin/deletion-request")
def send_deletion_request(
    payload: DeletionRequestPayload,
    db: Session = Depends(get_db),
):
    """Admin selects flagged files for an employee and pushes a deletion-request
    notification to that employee's dashboard."""
    # Validate the target employee exists
    target = db.query(Employee).filter(
        Employee.employee_id == payload.target_employee_id
    ).first()
    if not target:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Target employee not found")

    file_count = len(payload.file_ids)
    auto_msg = payload.message or (
        f"Admin has flagged {file_count} file{'s' if file_count != 1 else ''}"
        f" for immediate deletion."
    )

    notif = Notification(
        employee_id=payload.target_employee_id,
        admin_id=payload.admin_employee_id,
        message=auto_msg,
        file_ids=_json.dumps(payload.file_ids),
        status="unread",
        created_at=_dt.now(),
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)

    return {
        "status": "success",
        "notification_id": notif.id,
        "message": f"Deletion request sent to {target.first_name} {target.last_name}",
    }


@app.get("/api/employee/notifications/{employee_id}")
def get_employee_notifications(employee_id: str, db: Session = Depends(get_db)):
    """Return all notifications for an employee, unread first."""
    notifs = (
        db.query(Notification)
        .filter(Notification.employee_id == employee_id)
        .order_by(Notification.status.asc(), Notification.created_at.desc())
        .limit(50)
        .all()
    )
    results = []
    for n in notifs:
        created = n.created_at
        created_str = created.isoformat() if created else None
        results.append({
            "id": n.id,
            "message": n.message,
            "file_ids": _json.loads(n.file_ids) if n.file_ids else [],
            "status": n.status,
            "admin_id": n.admin_id,
            "created_at": created_str,
        })
    unread_count = sum(1 for n in notifs if n.status == "unread")
    return {"notifications": results, "unread_count": unread_count}


@app.post("/api/employee/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db)):
    """Mark a single notification as read."""
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notif:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.status == "unread":
        notif.status = "read"
        db.commit()
    return {"status": "success"}


@app.get("/api/compliance-score/{employee_id}")
def get_compliance_score(employee_id: str, db: Session = Depends(get_db)):
    """Compute a Data Hygiene Score (0-100) for an employee.

    Heuristic:
      - Start at 100
      - Penalise each expired file based on how many days overdue
      - Penalise each unresolved pending finding
      - Reward files that were deleted/resolved promptly
    """
    now = _dt.now()
    score = 100.0

    files = db.query(FileMetadata).filter(
        FileMetadata.owner_employee_id == employee_id
    ).all()

    total_files = len(files)
    expired_count = 0
    pending_findings_count = 0
    resolved_quickly = 0

    for f in files:
        # ── Retention deadline penalty ────────────────────────────────
        deadline = f.retention_deadline
        if deadline:
            if isinstance(deadline, str):
                try:
                    deadline = _dt.fromisoformat(deadline)
                except ValueError:
                    deadline = None
            if deadline and hasattr(deadline, 'tzinfo') and getattr(deadline, 'tzinfo', None):
                deadline = deadline.replace(tzinfo=None)

            if deadline and deadline < now:
                expired_count += 1
                days_overdue = (now - deadline).days
                if days_overdue <= 2:
                    score -= 3
                elif days_overdue <= 7:
                    score -= 8
                elif days_overdue <= 30:
                    score -= 15
                else:
                    score -= 25

        # ── Pending findings penalty ──────────────────────────────────
        findings = db.query(Finding).filter(
            Finding.file_id == f.id,
            Finding.status == "Pending",
        ).all()
        pending_findings_count += len(findings)
        score -= 5 * len(findings)

        # ── Reward quick resolution ───────────────────────────────────
        resolved = db.query(Finding).filter(
            Finding.file_id == f.id,
            Finding.status.like("%Completed%"),
        ).all()
        resolved_quickly += len(resolved)
        score += 2 * len(resolved)  # bonus

    # Clamp
    score = max(0, min(100, score))
    score = round(score)

    # Determine grade label
    if score >= 80:
        grade = "Excellent"
    elif score >= 60:
        grade = "Good"
    elif score >= 40:
        grade = "Needs Attention"
    else:
        grade = "Critical"

    return {
        "employee_id": employee_id,
        "score": score,
        "grade": grade,
        "breakdown": {
            "total_files": total_files,
            "expired_files": expired_count,
            "pending_findings": pending_findings_count,
            "resolved_actions": resolved_quickly,
        },
    }


# ── OCR Image Scanning ──────────────────────────────────────────────────────

from fastapi import UploadFile, File

# Allowed image MIME types for the OCR endpoint
_ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "image/gif",
}


@app.post("/api/scan/image")
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
    from fastapi import HTTPException

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


@app.get("/api/scan/image/cache-stats")
def ocr_cache_stats():
    """Return basic OCR cache statistics (admin/debug endpoint)."""
    from src.ocr_scanner import get_cache_stats
    return get_cache_stats()


@app.post("/api/scan/image/clear-cache")
def ocr_clear_cache():
    """Flush the OCR result cache (admin endpoint)."""
    from src.ocr_scanner import clear_cache
    evicted = clear_cache()
    return {"status": "success", "evicted_entries": evicted}

