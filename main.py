
from fastapi import FastAPI, Depends, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import database

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





from sqlalchemy import func
from database import FileMetadata, Finding

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