
from fastapi import FastAPI, Depends, Request, Form, Response, Cookie
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
        context={"employee_id": employee_id, "user": user}
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




from database import Employee, FileMetadata, Finding

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


# ── In-memory search database ──────────────────────────────────────
_search_db: List[Dict[str, Any]] = []

@app.on_event("startup")
async def load_search_data():
    global _search_db
    data_file = "tests/test_cases/workflow_input_test_cases.json"
    if os.path.exists(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # If it's the old format (list of findings)
                if isinstance(data, list):
                    _search_db = data
                # If it's the new workflow_input format
                elif isinstance(data, dict) and "cases" in data:
                    _search_db = []
                    import uuid
                    for case in data["cases"]:
                        doc = case.get("document", {})
                        expected = case.get("expected", {})
                        
                        file_id = doc.get("file_name", f"doc_{case.get('test_id')}")
                        content = doc.get("content", "")
                        
                        # Generate a mock finding for each expected data type
                        expected_types = expected.get("challenge_personal_data_present", [])
                        
                        # Assign some files to our dummy employee BX-21842 so the dashboard populates
                        assigned_owner = "BX-21842" if "Employee profile file" in case.get("description", "") else "unknown"
                        
                        for pii_type in expected_types:
                            finding = {
                                "finding_id": str(uuid.uuid4()),
                                "file_id": file_id,
                                "type": pii_type,
                                "value": f"[{pii_type.upper()} MOCK VALUE]",
                                "context": content[:100] + "...",
                                "risk_level": "high" if pii_type in ("tax_id", "iban", "credit_card") else "medium",
                                "assigned_owner": assigned_owner,
                                "owner_resolved": False
                            }
                            _search_db.append(finding)

                    print(f"Loaded {len(_search_db)} findings from test cases into search DB.")
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
        
        if type:
            item_type = item.get("type", "").lower()
            category_match = False
            if type == "passport" and ("passport" in item_type or "id" in item_type):
                category_match = True
            elif type == "financial" and ("iban" in item_type or "credit" in item_type or "bank" in item_type or "tax" in item_type):
                category_match = True
            elif type == "contact" and ("phone" in item_type or "email" in item_type or "address" in item_type):
                category_match = True
            elif type == "medical" and "medical" in item_type:
                category_match = True
            elif type == "travel" and "travel" in item_type:
                category_match = True
            elif type == "other":
                if not any(kw in item_type for kw in ["passport", "id", "iban", "credit", "bank", "tax", "phone", "email", "address", "medical", "travel"]):
                    category_match = True
            elif type == item_type:
                category_match = True
                
            if not category_match:
                continue

        if owner and item.get("assigned_owner", "") != owner:
            continue
        if resolved is not None and bool(item.get("owner_resolved", False)) != resolved:
            continue
        if q_lower:
            val = item.get("value", "").lower()
            ctx = item.get("context", "").lower()
            fid = item.get("file_id", "").lower()
            # Also search inside type for the query
            itype = item.get("type", "").lower()
            if q_lower not in val and q_lower not in ctx and q_lower not in fid and q_lower not in itype:
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
