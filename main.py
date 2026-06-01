import os
from dotenv import load_dotenv
load_dotenv()

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
    from database import engine, SessionLocal, FileMetadata, ScanJob
    import shutil
    import os
    import threading
    import uuid
    from api import _run_background_scan
    import seed_json_data
    
    cache_path = "bosch_gdpr.cache.db"
    db_path = "bosch_gdpr.db"
    
    from database import ActiveConnection
    try:
        if os.path.exists(cache_path):
            print("Cache found! Restoring instant cached database...")
            engine.dispose()
            shutil.copy2(cache_path, db_path)
            for ext in ['-wal', '-shm']:
                f = f"{db_path}{ext}"
                if os.path.exists(f):
                    os.remove(f)
            print("Cache restored successfully.")
        else:
            db = SessionLocal()
            try:
                has_data = db.query(FileMetadata).count() > 0
                is_connected = db.query(ActiveConnection).first() is not None

                if has_data or is_connected:
                    # Preserve existing data (incl. appended external imports)
                    # across restarts — do NOT auto-scan over it.
                    print(
                        f"Startup: data already present "
                        f"(files={db.query(FileMetadata).count()}, "
                        f"connected={is_connected}). Skipping auto-scan."
                    )
                else:
                    print("No cache found. Database is empty. Seeding baseline data and starting full background scan...")
                    seed_json_data.seed_data()

                    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
                    db.add(ScanJob(scan_id=scan_id, status="pending"))
                    db.commit()

                    print(f"Starting 104k file scan (ID: {scan_id}) in background...")
                    threading.Thread(
                        target=_run_background_scan,
                        args=(scan_id, "./strict_drive", "full", "layered", False),
                        daemon=True,
                    ).start()
            finally:
                db.close()
    except Exception as e:
        print(f"Error on startup: {e}")
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
    
    # Resolve name or email to proper BX-XXXXX ID
    resolved_emp = None
    if employee_id:
        if "@" in employee_id:
            resolved_emp = db.query(Employee).filter(Employee.email == employee_id).first()
        elif not employee_id.startswith("BX-"):
            parts = employee_id.strip().split(" ", 1)
            if len(parts) == 2:
                resolved_emp = db.query(Employee).filter(
                    Employee.first_name.ilike(parts[0]),
                    Employee.last_name.ilike(parts[1])
                ).first()
            if not resolved_emp:
                resolved_emp = db.query(Employee).filter(
                    (Employee.first_name.ilike(employee_id)) |
                    (Employee.last_name.ilike(employee_id))
                ).first()
        else:
            resolved_emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()

    if resolved_emp:
        employee_id = resolved_emp.employee_id

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
def get_user_details(
    employee_id: str, 
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    if not session_emp_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    # Resolve name or email to proper BX-XXXXX ID
    resolved_emp = None
    if employee_id and employee_id != "all":
        if "@" in employee_id:
            resolved_emp = db.query(Employee).filter(Employee.email == employee_id).first()
        elif not employee_id.startswith("BX-"):
            parts = employee_id.strip().split(" ", 1)
            if len(parts) == 2:
                resolved_emp = db.query(Employee).filter(
                    Employee.first_name.ilike(parts[0]),
                    Employee.last_name.ilike(parts[1])
                ).first()
            if not resolved_emp:
                resolved_emp = db.query(Employee).filter(
                    (Employee.first_name.ilike(employee_id)) |
                    (Employee.last_name.ilike(employee_id))
                ).first()
        else:
            resolved_emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()

    if resolved_emp:
        employee_id = resolved_emp.employee_id

    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        session_emp_id = "BX-ADMIN"
    if session_emp_id != "BX-ADMIN" and session_emp_id != employee_id and employee_id != "all":
        raise HTTPException(status_code=403, detail="Forbidden")
    if session_emp_id != "BX-ADMIN" and employee_id == "all":
        raise HTTPException(status_code=403, detail="Forbidden")

    if employee_id == "all":
        # Cap "all" requests to prevent massive OOM payloads
        user_files = db.query(FileMetadata).filter(
            ~FileMetadata.file_path.startswith("[DELETED]")
        ).limit(500).all()
        file_ids = [f.id for f in user_files]
        all_findings = db.query(Finding).filter(Finding.file_id.in_(file_ids)).all()
    else:
        user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()
        all_findings = db.query(Finding).join(
            FileMetadata, Finding.file_id == FileMetadata.id
        ).filter(
            FileMetadata.owner_employee_id == employee_id,
            ~FileMetadata.file_path.startswith("[DELETED]")
        ).all()
        
    findings_by_file = {}
    for finding in all_findings:
        findings_by_file.setdefault(finding.file_id, []).append(finding)
        # Also map string IDs just in case
        findings_by_file.setdefault(str(finding.file_id), []).append(finding)
        
    file_results = []
    
    for file in user_files:
        # Skip files that have been physically deleted
        file_path = getattr(file, "file_path", "")
        if file_path.startswith("[DELETED]"):
            continue
            
        file_findings = findings_by_file.get(file.id, [])
        findings_list = []
        
        for f in file_findings:
            status = getattr(f, "review_status", None) or getattr(f, "status", "pending_review")
            if status == "deleted" or getattr(f, "status", "") == "deleted":
                continue
                
            findings_list.append({
                "finding_id": getattr(f, "finding_uid", None) or str(f.id),
                "category": getattr(f, "category", None) or getattr(f, "type", ""),
                "flagged_snippet": getattr(f, "flagged_snippet", None) or getattr(f, "value", ""),
                "reasoning": getattr(f, "reasoning", None) or getattr(f, "context", ""),
                "risk_level": getattr(f, "risk_level", "low"),
                "status": status,
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
def get_admin_kpis(
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        session_emp_id = "BX-ADMIN"
    if session_emp_id != "BX-ADMIN":
        raise HTTPException(status_code=403, detail="Forbidden")
        
    from datetime import datetime, timedelta
    
    # 1 & 2. Total active files and total volume
    stats = db.query(
        func.count(FileMetadata.id),
        func.sum(FileMetadata.size_bytes)
    ).filter(
        ~FileMetadata.file_path.like("[DELETED]%")
    ).first()
    
    total_files = stats[0] or 0
    total_bytes = stats[1] or 0
    total_volume_gb = round(total_bytes / (1024 ** 3), 4) # Convert bytes to GB
    
    # 3. Total files with findings
    flagged_files = db.query(func.count(func.distinct(Finding.file_id))).join(
        FileMetadata, Finding.file_id == FileMetadata.id
    ).filter(
        ~FileMetadata.file_path.like("[DELETED]%"),
        Finding.status != 'deleted', 
        Finding.review_status != 'deleted'
    ).scalar() or 0
    
    # 4. Expiration stats
    now = datetime.now()
    thirty_days = now + timedelta(days=30)
    
    expiring_soon = 0
    delete_candidates = 0
    
    files_with_deadline = db.query(FileMetadata.retention_deadline).filter(
        ~FileMetadata.file_path.like("[DELETED]%"),
        FileMetadata.retention_deadline.isnot(None)
    ).all()
    
    for (deadline_val,) in files_with_deadline:
        deadline = deadline_val
        if deadline:
            if isinstance(deadline, str):
                try:
                    deadline = datetime.fromisoformat(deadline)
                except ValueError:
                    deadline = None
            if deadline and getattr(deadline, 'tzinfo', None):
                deadline = deadline.replace(tzinfo=None)
            
            if deadline and deadline < now:
                delete_candidates += 1
            elif deadline and deadline <= thirty_days:
                expiring_soon += 1

    # 5. Get a summary of the most recent findings WITH MASKING applied
    recent_findings = db.query(Finding).order_by(Finding.id.desc()).limit(10).all()
    
    # Optimize N+1 lookup for file names
    recent_file_ids = [f.file_id for f in recent_findings if f.file_id]
    file_records = db.query(FileMetadata).filter(FileMetadata.id.in_(recent_file_ids)).all()
    file_map = {f.id: f for f in file_records}
    
    safe_findings = []
    for finding in recent_findings:
        # Get filename
        file_record = file_map.get(finding.file_id)
        if file_record and file_record.file_path:
            filename = file_record.file_path.split("/")[-1].split("\\")[-1]
        else:
            filename = finding.file_id_str or "Unknown File"
            
        safe_findings.append({
            "finding_id": finding.id,
            "file_name": filename,
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
            "expiring_soon": expiring_soon,
            "delete_candidates": delete_candidates
        },
        "recent_alerts": safe_findings
    }

@app.get("/api/admin/employees/search")
def search_employees(
    q: str, 
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        session_emp_id = "BX-ADMIN"
    if session_emp_id != "BX-ADMIN":
        raise HTTPException(status_code=403, detail="Forbidden")
        
    if not q:
        return []
    
    q_lower = f"%{q.lower()}%"
    from sqlalchemy import or_, func, desc
    
    # 1. Fetch matching employees and their file counts first
    matching_emps_query = (
        db.query(Employee, func.count(FileMetadata.id).label("file_count"))
        .outerjoin(FileMetadata, (FileMetadata.owner_employee_id == Employee.employee_id) & ~FileMetadata.file_path.startswith("[DELETED]"))
        .filter(
            or_(
                func.lower(Employee.first_name).like(q_lower),
                func.lower(Employee.last_name).like(q_lower),
                func.lower(Employee.email).like(q_lower),
                func.lower(Employee.employee_id).like(q_lower),
                func.lower(Employee.first_name + " " + Employee.last_name).like(q_lower),
            )
        )
        .group_by(Employee.id)
        .order_by(desc("file_count"), Employee.last_name)
        .limit(50)
        .all()
    )
    
    # Extract the matched employee IDs
    emp_ids = [emp.employee_id for emp, _ in matching_emps_query]
    
    if not emp_ids:
        return []

    # 2. Fetch findings count ONLY for these specific matched employees
    findings_counts = (
        db.query(
            FileMetadata.owner_employee_id,
            func.count(Finding.id)
        )
        .join(Finding, Finding.file_id == FileMetadata.id)
        .filter(
            FileMetadata.owner_employee_id.in_(emp_ids),
            ~FileMetadata.file_path.startswith("[DELETED]"),
            Finding.status != 'deleted',
            Finding.review_status != 'deleted'
        )
        .group_by(FileMetadata.owner_employee_id)
        .all()
    )
    
    findings_map = {emp_id: count for emp_id, count in findings_counts}
    
    results = []
    for emp, file_count in matching_emps_query:
        results.append({
            "employee_id": emp.employee_id,
            "first_name": emp.first_name,
            "last_name": emp.last_name,
            "email": emp.email,
            "department": emp.department,
            "location": emp.location,
            "file_count": file_count,
            "findings_count": findings_map.get(emp.employee_id, 0),
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
def get_employee_files(
    employee_id: str, 
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    if not session_emp_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    from datetime import datetime
    
    # Resolve name or email to proper BX-XXXXX ID
    resolved_emp = None
    if employee_id:
        if "@" in employee_id:
            resolved_emp = db.query(Employee).filter(Employee.email == employee_id).first()
        elif not employee_id.startswith("BX-"):
            parts = employee_id.strip().split(" ", 1)
            if len(parts) == 2:
                resolved_emp = db.query(Employee).filter(
                    Employee.first_name.ilike(parts[0]),
                    Employee.last_name.ilike(parts[1])
                ).first()
            if not resolved_emp:
                resolved_emp = db.query(Employee).filter(
                    (Employee.first_name.ilike(employee_id)) |
                    (Employee.last_name.ilike(employee_id))
                ).first()
        else:
            resolved_emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()

    if resolved_emp:
        employee_id = resolved_emp.employee_id

    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        session_emp_id = "BX-ADMIN"
    if session_emp_id != "BX-ADMIN" and session_emp_id != employee_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # 1. Find all files owned by this specific employee
    user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()
    user_files = [f for f in user_files if not getattr(f, "file_path", "").startswith("[DELETED]")]
    user_file_ids = [f.id for f in user_files]
    
    if not user_file_ids:
        return {"message": "No files found for this user.", "findings": []}

    # 2. Find all active GDPR flags for those specific files
    #    Accept both 'pending_review' and 'pending' since the DB may use either
    #    value depending on which ingestion path created the findings.
    from sqlalchemy import or_
    user_findings = db.query(Finding).filter(
        Finding.file_id.in_(user_file_ids),
        or_(
            Finding.review_status == "pending_review",
            Finding.review_status == "pending",
            Finding.status == "Pending",
            Finding.status == "pending_review",
        )
    ).all()
    
    # 3. Format the response for the frontend
    file_map = {f.id: f for f in user_files}
    results = []
    for finding in user_findings:
        # We need the file name to show the user which file has the issue
        file_record = file_map.get(finding.file_id)
        if not file_record:
            continue
        
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
            "file_path": file_record.file_path,
            "category": finding.category,
            "flagged_snippet": finding.flagged_snippet, # UNMASKED: The employee is allowed to see their own data
            "match_context": finding.reasoning or finding.context or "",  # Context around the match for frontend display
            "reasoning": finding.reasoning,
            "risk_level": finding.risk_level or "medium",
            "recommended_action": finding.recommended_action or "review",
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
    if file_meta.owner_employee_id != req.employee_id and req.employee_id != "all":
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
        if not file_meta.file_path.startswith("[DELETED]"):
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
        
    if file_meta.owner_employee_id != req.employee_id and req.employee_id != "all":
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
    target_dir: str = "./strict_drive"
    previous_scan_id: Optional[str] = None  # e.g. "scan-a1b2c3d4"


@app.post("/api/admin/trigger-scan")
def trigger_manual_scan(
    req: TriggerScanRequest = TriggerScanRequest(),
    db: Session = Depends(get_db),
):
    """Run a fast streaming scan in the background."""
    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
    job = ScanJobORM(
        scan_id=scan_id,
        status="pending",
        options_json=json.dumps({"mode": "full", "ai_mode": "layered", "strict_hash": False}),
        created_at=datetime.now().isoformat()
    )
    db.add(job)
    db.commit()

    import threading
    threading.Thread(
        target=_run_background_scan,
        kwargs={
            "scan_id": scan_id,
            "folder_path": req.target_dir,
            "mode": "full",
            "ai_mode": "layered",
            "strict_hash": False
        },
        daemon=True,
    ).start()

    return {
        "status": "success",
        "message": "Scan started in background",
        "scan_id": scan_id
    }


# ── Memory-Safe Extraction Pipeline (New) ────────────────────────────────────
# This endpoint uses the new src/extractor.py module which:
#   1. Reads files via generators (O(page) memory for PDFs)
#   2. Uses pre-compiled regex (O(1) compilation cost)
#   3. Returns a strict JSON contract for Admin + User views
# ─────────────────────────────────────────────────────────────────────────────

# Cache the latest extraction result in-process to avoid re-scanning
# for dashboard refreshes.  This is intentionally a module-level dict,
# not a DB table, so it resets on server restart.
_latest_extraction_result: dict = {}


class TriggerExtractionRequest(PydanticBaseModel):
    """Body for POST /api/admin/trigger-extraction."""
    target_dir: str = "./strict_drive"


@app.post("/api/admin/trigger-extraction")
def trigger_extraction(
    req: TriggerExtractionRequest = TriggerExtractionRequest(),
    db: Session = Depends(get_db),
):
    """Run the memory-safe extraction pipeline on a target directory.

    This endpoint:
      1. Loads owner_hints.json for file→owner mapping.
      2. Calls scan_directory() which processes files ONE AT A TIME
         using generator-based chunked reading (constant memory).
      3. Persists new findings to the SQLite database.
      4. Returns the strict JSON contract:
         - admin_aggregates: totals for the dashboard KPI blocks.
         - user_file_details: per-file owner + findings + context.

    The scan handles unlimited data — a 5 TB drive uses the same
    RAM as a 5 MB folder because files are never fully loaded.
    """
    global _latest_extraction_result
    from pathlib import Path as _Path
    from src.extractor import scan_directory
    import json

    target_dir = req.target_dir
    if not _Path(target_dir).exists():
        from fastapi import HTTPException
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
    import os
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


@app.get("/api/admin/extraction-results")
def get_extraction_results():
    """Return the latest cached extraction results without re-scanning.

    Use this for dashboard refreshes — it's instant because it reads
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
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    if not session_emp_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    """Search findings from the live SQLite database."""
    query = db.query(Finding)

    if session_emp_id in ("BX-ADMIN", "BX-17335"):
        session_emp_id = "BX-ADMIN"
    if session_emp_id != "BX-ADMIN":
        query = query.join(FileMetadata, Finding.file_id == FileMetadata.id)\
                     .filter(FileMetadata.owner_employee_id == session_emp_id)

    if risk_level:
        query = query.filter(Finding.risk_level == risk_level)
    if type:
        if type == "financial":
            query = query.filter(Finding.type.in_(["iban", "credit_card", "bank"]))
        elif type == "contact":
            query = query.filter(Finding.type.in_(["email", "phone", "address"]))
        elif type == "passport":
            query = query.filter(Finding.type.in_(["passport", "id_card", "ssn", "tax_id"]))
        elif type == "other":
            query = query.filter(~Finding.type.in_([
                "iban", "credit_card", "bank",
                "email", "phone", "address",
                "passport", "id_card", "ssn", "tax_id"
            ]))
        else:
            query = query.filter(Finding.type == type)

    if owner and session_emp_id == "BX-ADMIN":
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

    # 1. Get total count of distinct files matching filters
    file_id_query = query.with_entities(Finding.file_id).distinct()
    total_count = file_id_query.count()
    
    # 2. Paginate the distinct file_ids
    # Using order_by to ensure consistent pagination order
    paginated_file_ids_raw = file_id_query.order_by(Finding.file_id.desc()).offset(skip).limit(limit).all()
    paginated_file_ids = [r[0] for r in paginated_file_ids_raw if r[0] is not None]
    
    if paginated_file_ids:
        # 3. Fetch ONLY findings for those files
        page_results = query.filter(Finding.file_id.in_(paginated_file_ids)).all()
    else:
        page_results = []
    
    # Group findings by file to return exactly one entry per file
    grouped_files = {}
    for res in page_results:
        file_id = res.file_id_str or str(res.file_id)
        if file_id not in grouped_files:
            grouped_files[file_id] = {
                "finding_id": res.finding_uid or str(res.id),
                "db_file_id": res.file_id,
                "file_id": file_id,
                "types": set(),
                "values": set(),
                "context": res.context or "unknown",
                "risk_level": res.risk_level or "low",
                "status": res.review_status or res.status or "pending_review",
                "assigned_owner": res.assigned_owner or "",
                "owner_resolved": res.owner_resolved or False,
            }
        
        # Aggregate types
        typ = res.type or res.category or ""
        if typ:
            grouped_files[file_id]["types"].add(typ)
            
        # Determine highest risk
        rl = (res.risk_level or "low").lower()
        curr_risk = grouped_files[file_id]["risk_level"].lower()
        if rl == "high" or (rl == "medium" and curr_risk == "low"):
            grouped_files[file_id]["risk_level"] = rl

    # Format the results
    paginated = []
    risk_breakdown: Dict[str, int] = {}
    
    for file_id, data in grouped_files.items():
        types_list = list(data["types"])
        if len(types_list) > 1:
            display_type = "multiple"
        elif len(types_list) == 1:
            display_type = types_list[0]
        else:
            display_type = ""
            
        rl = data["risk_level"]
        risk_breakdown[rl] = risk_breakdown.get(rl, 0) + 1
            
        paginated.append({
            "finding_id": data["finding_id"],
            "db_file_id": data["db_file_id"],
            "file_id": file_id,
            "type": display_type,
            "value": "",
            "context": data["context"],
            "risk_level": rl,
            "status": data["status"],
            "assigned_owner": data["assigned_owner"],
            "owner_resolved": data["owner_resolved"],
        })

    return {
        "results": paginated,
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
    
    files = [f for f in files if not getattr(f, "file_path", "").startswith("[DELETED]")]

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
    '''Return basic OCR cache statistics (admin/debug endpoint).'''
    from src.ocr_scanner import get_cache_stats
    return get_cache_stats()


@app.post("/api/scan/image/clear-cache")
def ocr_clear_cache():
    """Flush the OCR result cache (admin endpoint)."""
    from src.ocr_scanner import clear_cache
    evicted = clear_cache()
    return {"status": "success", "evicted_entries": evicted}

from fastapi.responses import FileResponse
from fastapi import Cookie
from typing import Optional

@app.get("/api/files/{file_id}/view")
def view_file_content(
    file_id: int,
    session_emp_id: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    from fastapi import HTTPException
    import os
    
    if not session_emp_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    file_meta = db.query(FileMetadata).filter(FileMetadata.id == file_id).first()
    if not file_meta:
        raise HTTPException(status_code=404, detail="File Not Found")
        
    # ONLY the file owner can view the file. Admin CANNOT view the raw file!
    # This ensures GDPR compliance where employees have the right to access their own data,
    # but admins cannot arbitrarily snoop on raw file contents.
    if file_meta.owner_employee_id != session_emp_id:
        raise HTTPException(status_code=403, detail="Forbidden: You can only view your own files.")
        
    file_path = file_meta.file_path
    if file_path.startswith("[DELETED]") or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File has been physically deleted.")
        
    return FileResponse(file_path)

import uuid
import json
import threading
from datetime import datetime
from database import ActiveConnection as ActiveConnectionORM, ScanJob as ScanJobORM
from api import _run_background_scan, ConnectRequest, ConnectSummary

def _link_from_config(source_type: str, cfg: dict) -> str | None:
    """Pull the public share link out of the modal's per-source config field.

    The Connect modal sends the link in shared_drive_id (Drive),
    user_id (OneDrive) or site_id (SharePoint). Mock configs carry "mock".
    """
    for key in ("shared_drive_id", "url", "user_id", "site_id", "drive_id"):
        val = (cfg.get(key) or "").strip()
        if val and val.lower() != "mock":
            return val
    return None


@app.post("/api/connect", response_model=ConnectSummary)
async def connect_source(req: ConnectRequest, db: Session = Depends(get_db)):
    active_conn = db.query(ActiveConnectionORM).first()
    if not active_conn:
        active_conn = ActiveConnectionORM()
        db.add(active_conn)
    active_conn.source_type = req.source_type
    active_conn.connection_config = json.dumps(req.connection_config)
    db.commit()

    # Connecting an external source APPENDS to the current dataset (no wipe).

    scan_id = f"scan-{uuid.uuid4().hex[:8]}"
    job = ScanJobORM(
        scan_id=scan_id,
        status="pending",
        options_json=json.dumps({"mode": "full", "ai_mode": "layered", "strict_hash": False}),
        created_at=datetime.now().isoformat()
    )
    db.add(job)
    db.commit()

    threading.Thread(
        target=_run_background_scan,
        kwargs={
            "scan_id": scan_id,
            "folder_path": "./strict_drive",
            "mode": "full",
            "ai_mode": "layered",
            "strict_hash": False,
            "source_type": req.source_type,
            "connection_config": req.connection_config or {}
        },
        daemon=True,
    ).start()
    return ConnectSummary(source_type=req.source_type, message="Connected and scan started.")

@app.post("/api/disconnect", response_model=ConnectSummary)
async def disconnect_source(db: Session = Depends(get_db)):
    from database import engine
    import shutil
    import os
    
    try:
        active_conn = db.query(ActiveConnectionORM).first()
        if active_conn:
            db.delete(active_conn)
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Warning: Could not delete active_conn, likely locked: {e}")
        
    cache_path = "bosch_gdpr.cache.db"
    db_path = "bosch_gdpr.db"
    
    if os.path.exists(cache_path):
        # We must fully close the session to release any SQLite file locks
        db.close()
        engine.dispose()
        
        try:
            # Overwrite the db file
            shutil.copy2(cache_path, db_path)
            for ext in ['-wal', '-shm']:
                f = f"{db_path}{ext}"
                if os.path.exists(f):
                    os.remove(f)
        except Exception as e:
            print(f"Error copying cache: {e}")
            
        return ConnectSummary(source_type="local", message="Disconnected and local cache restored instantly.")

    else:
        # Fallback if no cache exists
        from database import Finding, FileMetadata
        db.query(Finding).delete()
        db.query(FileMetadata).delete()
        db.commit()
        
        scan_id = f"scan-{uuid.uuid4().hex[:8]}"
        job = ScanJobORM(
            scan_id=scan_id,
            status="pending",
            options_json=json.dumps({"mode": "full", "ai_mode": "layered", "strict_hash": False}),
            created_at=datetime.now().isoformat()
        )
        db.add(job)
        db.commit()

        threading.Thread(
            target=_run_background_scan,
            args=(scan_id, "./strict_drive", "full", "layered", False),
            daemon=True,
        ).start()
        
        return ConnectSummary(source_type="local", message="Disconnected and local scan started (No cache).")
@app.get("/api/connection_status")
async def get_connection_status(db: Session = Depends(get_db)):
    active_conn = db.query(ActiveConnectionORM).first()
    if active_conn:
        return {"source_type": active_conn.source_type}
    return {"source_type": "local"}
