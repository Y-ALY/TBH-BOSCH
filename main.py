# =============================================================================
# main.py — TBH-BOSCH Web Dashboard & API
# =============================================================================
#
# DECISION (Agent 3 - API Boundary Cleanup):
#   main.py is the SINGLE deployable FastAPI application.  It serves the
#   Jinja2 web dashboard (login, admin, employee views) AND a broad set of
#   API routes (KPIs, employee management, notifications, search, health).
#
#   The dedicated api.py (GDPR Data Discovery API) is a SEPARATE FastAPI
#   application that remains for local development and testing.  It is NOT
#   required for production — all scan-related routes that were previously
#   duplicated in main.py are now served via the app.routes.scan router
#   mounted below, so a single `uvicorn main:app` process can handle both
#   dashboard pages and the scan/review API.
#
#   Both applications share the same database.py models and src/ scan
#   engine.  In production, deploy main.py as the primary entry point.
# =============================================================================

from fastapi import FastAPI, Depends, Request, Form, Response, Cookie
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import get_db, Employee, FileMetadata, Finding, Notification

from typing import Optional, List, Dict, Any

app = FastAPI(title="Bosch GDPR Scan Engine API")


@app.on_event("startup")
def startup_event():
    """Delegate database seeding to app.startup.seed_on_startup."""
    from app.startup import seed_on_startup
    seed_on_startup(app)


app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(directory="templates")

# DEMO-ONLY: Wildcard CORS allows any origin to call this API.
# For production, restrict allow_origins to your frontend's domain(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (good for hackathons)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],
)

# ── Mount scan-related API routes (extracted from this file) ─────────────────
from app.routes.scan import router as scan_router
app.include_router(scan_router)





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
    # DEMO-ONLY: Plaintext password comparison against DB. No hashing, no rate limiting.
    # See docs/production-readiness.md for production auth requirements.
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

    # DEMO-ONLY: Unsigned cookie — anyone can forge a session by setting session_emp_id.
    # For production: use signed sessions (e.g., starlette SessionMiddleware) or JWT tokens.
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

    if session_emp_id != "BX-ADMIN" and session_emp_id != employee_id and employee_id != "all":
        raise HTTPException(status_code=403, detail="Forbidden")
    if session_emp_id != "BX-ADMIN" and employee_id == "all":
        raise HTTPException(status_code=403, detail="Forbidden")

    if employee_id == "all":
        user_files = db.query(FileMetadata).all()
    else:
        user_files = db.query(FileMetadata).filter(FileMetadata.owner_employee_id == employee_id).all()

    file_results = []

    for file in user_files:
        # Skip files that have been physically deleted
        file_path = getattr(file, "file_path", "")
        if file_path.startswith("[DELETED]"):
            continue

        file_findings = db.query(Finding).filter(Finding.file_id == file.id).all()
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
    if session_emp_id != "BX-ADMIN":
        raise HTTPException(status_code=403, detail="Forbidden")

    from datetime import datetime, timedelta

    # 1. Fetch all files and filter out deleted ones
    all_files = db.query(FileMetadata).all()
    active_files = [f for f in all_files if not getattr(f, "file_path", "").startswith("[DELETED]")]

    total_files = len(active_files)

    # 2. Total data volume in bytes, converted to GB
    total_bytes = sum(getattr(f, "size_bytes", 0) or 0 for f in active_files)
    total_volume_gb = round(total_bytes / (1024 ** 3), 4) # Convert bytes to GB

    # 3. Total files with findings
    active_file_ids = [f.id for f in active_files]
    if active_file_ids:
        flagged_files = db.query(func.count(func.distinct(Finding.file_id))).filter(
            Finding.file_id.in_(active_file_ids),
            Finding.status != 'deleted',
            Finding.review_status != 'deleted'
        ).scalar() or 0
    else:
        flagged_files = 0

    # 4. Expiration stats
    now = datetime.now()
    thirty_days = now + timedelta(days=30)

    expiring_soon = 0
    delete_candidates = 0

    for f in active_files:
        deadline = f.retention_deadline
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

    safe_findings = []
    for finding in recent_findings:
        # Get filename
        file_record = db.query(FileMetadata).filter(FileMetadata.id == finding.file_id).first()
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
    if session_emp_id != "BX-ADMIN":
        raise HTTPException(status_code=403, detail="Forbidden")

    if not q:
        return []

    q_lower = f"%{q.lower()}%"
    from sqlalchemy import or_, func, desc

    # Query employees with LEFT JOIN to files to get file counts.
    # Sort by file_count DESC so employees who actually own files
    # appear first in the search results -- this is the key UX fix
    # that prevents zero-file employees from crowding out results.
    results_raw = (
        db.query(
            Employee,
            func.count(FileMetadata.id).label("file_count"),
        )
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

    results = []
    for emp, file_count in results_raw:
        # Count findings across this employee's files
        findings_count = 0
        if file_count > 0:
            emp_file_ids = [
                f.id for f in db.query(FileMetadata.id)
                .filter(FileMetadata.owner_employee_id == emp.employee_id, ~FileMetadata.file_path.startswith("[DELETED]"))
                .all()
            ]
            if emp_file_ids:
                findings_count = db.query(Finding).filter(
                    Finding.file_id.in_(emp_file_ids),
                    Finding.status != 'deleted',
                    Finding.review_status != 'deleted'
                ).count()

        results.append({
            "employee_id": emp.employee_id,
            "first_name": emp.first_name,
            "last_name": emp.last_name,
            "email": emp.email,
            "department": emp.department,
            "location": emp.location,
            "file_count": file_count,
            "findings_count": findings_count,
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
        "RETENTION JUSTIFICATION -- file=%s reason=%s project=%s admin=%s notes=%s",
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

    all_results = query.all()

    # Group findings by file to return exactly one entry per file
    grouped_files = {}
    for res in all_results:
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
    results = []
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

        results.append({
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

    total_count = len(results)
    paginated = results[skip : skip + limit]

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


class DeletionRequestPayload(BaseModel):
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


# ── Health Check ─────────────────────────────────────────────────────────────
# Used by Render for zero-downtime deploys and monitoring.


@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    """Quick health probe for Render / monitoring."""
    return {
        "status": "ok",
        "findings_loaded": db.query(Finding).count(),
        "files_loaded": db.query(FileMetadata).count(),
        "employees_loaded": db.query(Employee).count(),
    }
