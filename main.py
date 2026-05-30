
from fastapi import FastAPI, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import database

import json
import os
from typing import Optional, List, Dict, Any

app = FastAPI(title="Bosch GDPR Scan Engine API")

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




# Dependency to get the DB session
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    role: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)
):
    
    # Hackathon demo credentials

    if role == "employee":
        return RedirectResponse(
            url="/employee-dashboard",
            status_code=303
        )

    if role == "admin":
        return RedirectResponse(
            url="/admin-dashboard",
            status_code=303
        )

    return {"error": "Invalid login"}



@app.get("/employee-dashboard")
def employee_dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="employee_dashboard.html",
        context={}
    )


@app.get("/employee-directory")
def employee_directory(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="employee_directory.html",
        context={}
    )


@app.get("/user-details/{employee_id}")
def user_details(employee_id: str, request: Request):
    return templates.TemplateResponse(
        request=request,
        name="user_details.html",
        context={"employee_id": employee_id}
    )

from sqlalchemy import func
from database import FileMetadata, Finding

@app.get("/api/user-details/{employee_id}")
def get_user_details(employee_id: str):
    # Group findings from _search_db for this employee
    user_findings = [item for item in _search_db if item.get("assigned_owner") == employee_id]
    
    if not user_findings:
        return {"employee_id": employee_id, "files": [], "retention_deadline": None}
        
    findings_by_file = {}
    for finding in user_findings:
        f_id = finding.get("file_id", "Unknown File")
        if f_id not in findings_by_file:
            findings_by_file[f_id] = []
            
        findings_by_file[f_id].append({
            "finding_id": finding.get("finding_id"),
            "category": finding.get("type"),
            "flagged_snippet": finding.get("value"),
            "reasoning": finding.get("context"),
            "risk_level": finding.get("risk_level", "low"),
            "status": "Pending" if not finding.get("owner_resolved") else "Resolved",
            "confidence_score": 0.95
        })

    from datetime import datetime, timedelta
    
    file_results = []
    earliest_deadline = None
    
    for f_id, f_findings in findings_by_file.items():
        # Mock retention deadline based on highest risk level in the file
        highest_risk = "low"
        for f in f_findings:
            if f.get("risk_level") == "high":
                highest_risk = "high"
                break
            elif f.get("risk_level") == "medium":
                highest_risk = "medium"
                
        if highest_risk == "high":
            deadline = datetime.now() + timedelta(seconds=15)
        elif highest_risk == "medium":
            deadline = datetime.now() + timedelta(seconds=45)
        else:
            deadline = datetime.now() + timedelta(days=30)
            
        if earliest_deadline is None or deadline < earliest_deadline:
            earliest_deadline = deadline
            
        file_results.append({
            "file_id": f_id,
            "file_name": f_id,
            "file_path": f"/shared_drive/{employee_id}/{f_id}",
            "size_bytes": 1024 * 42, # Mock 42 KB
            "last_modified": (datetime.now() - timedelta(days=5)).isoformat(),
            "retention_deadline": deadline.isoformat(),
            "findings": f_findings
        })

    # Add a couple of dummy CLEAN files so the user sees not everything is a violation
    clean_files = ["project_proposal.docx", "annual_review.pdf", "meeting_notes.txt"]
    for idx, c_file in enumerate(clean_files):
        deadline = datetime.now() + timedelta(days=60 + idx * 10)
        if earliest_deadline is None or deadline < earliest_deadline:
            earliest_deadline = deadline
            
        file_results.append({
            "file_id": f"clean_{idx}",
            "file_name": c_file,
            "file_path": f"/shared_drive/{employee_id}/{c_file}",
            "size_bytes": 1024 * (15 + idx * 5),
            "last_modified": (datetime.now() - timedelta(days=1)).isoformat(),
            "retention_deadline": deadline.isoformat(),
            "findings": [] # Empty findings array means it's a clean file!
        })

    return {
        "employee_id": employee_id,
        "retention_deadline": earliest_deadline.isoformat() if earliest_deadline else None,
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
    return {"status": "success", "message": f"Processed {request.action} on file {request.file_id}"}




import scanner # Import the file you just made

@app.post("/api/admin/trigger-scan")
def trigger_manual_scan(db: Session = Depends(get_db)):
    # Point this to the dummy folder you created
    target_dir = "./mock_drive" 
    
    # Run the delta scan!
    results = scanner.run_delta_scan(target_dir, db)
    
    return {
        "status": "success", 
        "message": "Scan executed", 
        "details": results
    }




from database import Finding # Ensure this is imported at the top if it isn't already

@app.post("/api/admin/seed-dummy-data")
def seed_dummy_data(db: Session = Depends(get_db)):
    # Inject a fake GDPR violation so the frontend has something to display
    dummy_finding = Finding(
        file_id=2, # Tying it to test2.txt
        category="Passport Number",
        confidence_score=0.98,
        flagged_snippet="L9923481X",
        reasoning="Found standard passport format in onboarding doc.",
        status="Pending"
    )
    db.add(dummy_finding)
    db.commit()
    return {"message": "Dummy finding injected!"}


# ── In-memory search database ──────────────────────────────────────
_search_db: List[Dict[str, Any]] = []

@app.on_event("startup")
async def load_search_data():
    global _search_db
    data_file = "scan_results.json"
    if os.path.exists(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    _search_db = data
                    print(f"Loaded {len(_search_db)} findings into search DB.")
        except Exception as e:
            print(f"Error loading {data_file}: {e}")
    else:
        print(f"Warning: {data_file} not found. Search will return empty results.")

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
):
    q_lower = q.lower() if q else None
    filtered = []

    for item in _search_db:
        if risk_level and item.get("risk_level", "") != risk_level:
            continue
        if type and item.get("type", "") != type:
            continue
        if owner and item.get("assigned_owner", "") != owner:
            continue
        if resolved is not None and bool(item.get("owner_resolved", False)) != resolved:
            continue
        if q_lower:
            val = item.get("value", "").lower()
            ctx = item.get("context", "").lower()
            fid = item.get("file_id", "").lower()
            if q_lower not in val and q_lower not in ctx and q_lower not in fid:
                continue
        filtered.append(item)

    total_count = len(filtered)
    risk_breakdown = {}
    for res in filtered:
        rl = res.get("risk_level", "unknown")
        risk_breakdown[rl] = risk_breakdown.get(rl, 0) + 1

    paginated = filtered[skip : skip + limit]

    return {
        "results": paginated,
        "metadata": {
            "total_count": total_count,
            "risk_breakdown": risk_breakdown,
        },
    }