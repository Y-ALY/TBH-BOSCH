import json
import os
import uuid
import tempfile
import shutil
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Query, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import the existing scanner pipeline for dynamic file ingestion
from pii_filter import FastFilterPipeline
from pii_filter.state_manager import InMemoryStateManager
from pii_filter.file_ingestor import ingest_file

# Initialize the FastAPI application
app = FastAPI(
    title="GDPR Data Discovery Search API",
    description="Backend API for querying and filtering parsed GDPR findings.",
    version="1.0.0"
)

# Configure CORS so the frontend (React/Streamlit) can communicate with the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins per requirements
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models for Strict Type Hinting ---

class Finding(BaseModel):
    finding_id: str
    file_id: str
    type: str
    value: str
    context: str
    risk_level: str
    assigned_owner: str
    owner_resolved: bool

class SearchMetadata(BaseModel):
    total_count: int
    risk_breakdown: Dict[str, int]

class SearchResponse(BaseModel):
    results: List[Finding]
    metadata: SearchMetadata

# --- In-Memory Storage ---

# We store findings as a simple list of dicts for blazing fast in-memory filtering.
# For larger datasets, this could easily be swapped out with SQLite or a vector DB.
findings_db: List[Dict[str, Any]] = []

# --- API Endpoints and Events ---

@app.on_event("startup")
async def load_data():
    """
    Startup event to load `scan_results.json` into memory.
    This ensures that search queries are fast as they don't involve disk I/O.
    """
    global findings_db
    data_file = "scan_results.json"
    
    if os.path.exists(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    findings_db = data
                    print(f"Successfully loaded {len(findings_db)} findings into memory from {data_file}.")
                else:
                    print(f"Warning: Data in {data_file} is not a list. Check your pipeline output.")
        except Exception as e:
            print(f"Error loading {data_file}: {e}")
    else:
        print(f"Warning: {data_file} not found. Starting with an empty database.")
        print("Run the data discovery pipeline first to generate this file.")

@app.get("/api/search", response_model=SearchResponse)
async def search_findings(
    q: Optional[str] = Query(None, description="Full-text search term for 'value' and 'context'"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level (high, medium, low)"),
    type: Optional[str] = Query(None, description="Filter by finding type (e.g., email, iban)"),
    owner: Optional[str] = Query(None, description="Filter by assigned owner"),
    resolved: Optional[bool] = Query(None, description="Filter by resolution status"),
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=1000, description="Number of results to return per page")
):
    """
    Highly flexible search endpoint for findings.
    Performs filtering on the in-memory database and computes aggregate metadata.
    """
    global findings_db
    
    filtered_results = []
    
    # Pre-process the query string for case-insensitive matching
    q_lower = q.lower() if q else None
    
    # 1. Filter Data
    for item in findings_db:
        # Extract fields safely using .get() to avoid KeyErrors on malformed data
        item_type = item.get("type", "")
        item_risk = item.get("risk_level", "")
        item_owner = item.get("assigned_owner", "")
        item_resolved = bool(item.get("owner_resolved", False))
        item_val = item.get("value", "")
        item_ctx = item.get("context", "")
        
        # Exact match filters
        if risk_level and item_risk != risk_level:
            continue
        if type and item_type != type:
            continue
        if owner and item_owner != owner:
            continue
        if resolved is not None and item_resolved != resolved:
            continue
            
        # Full-text search filter (scans both 'value' and 'context')
        if q_lower:
            if q_lower not in item_val.lower() and q_lower not in item_ctx.lower():
                continue
                
        # If all conditions pass, include the finding
        filtered_results.append(item)
        
    # 2. Aggregate Metadata (Stats)
    total_count = len(filtered_results)
    risk_breakdown = {}
    
    for res in filtered_results:
        # Group by risk level for the UI charts
        r_level = res.get("risk_level", "unknown")
        risk_breakdown[r_level] = risk_breakdown.get(r_level, 0) + 1
        
    # 3. Apply Pagination (Skip and Limit)
    paginated_results = filtered_results[skip : skip + limit]
    
    # Convert dicts into Pydantic models for the response payload
    # This also acts as a data validation layer ensuring the frontend gets a strict schema
    validated_results = []
    for p in paginated_results:
        validated_results.append(Finding(
            finding_id=p.get("finding_id", "unknown"),
            file_id=p.get("file_id", "unknown"),
            type=p.get("type", "unknown"),
            value=p.get("value", ""),
            context=p.get("context", ""),
            risk_level=p.get("risk_level", "unknown"),
            assigned_owner=p.get("assigned_owner", "unassigned"),
            owner_resolved=p.get("owner_resolved", False)
        ))
    
    return SearchResponse(
        results=validated_results,
        metadata=SearchMetadata(
            total_count=total_count,
            risk_breakdown=risk_breakdown
        )
    )

@app.post("/api/upload")
async def upload_findings_files(file: List[UploadFile] = File(...)):
    """
    Upload multiple files or a folder to scan for PII, or upload a pre-parsed JSON findings file.
    Supports .json, .pdf, .docx, .txt, .md.
    If a JSON is uploaded, it REPLACES the findings database.
    If documents are uploaded, they are SCANNED and their findings are APPENDED to the database.
    """
    global findings_db
    
    supported_exts = {".json", ".pdf", ".docx", ".txt", ".md"}
    
    new_findings_count = 0
    scanned_files = 0
    
    # Even though it's called 'file' for frontend compatibility, it's a list of UploadFiles
    for f_obj in file:
        ext = os.path.splitext(f_obj.filename)[1].lower()
        
        if ext not in supported_exts:
            continue
            
        try:
            # Save uploaded file to a temporary file
            fd, temp_path = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as f:
                shutil.copyfileobj(f_obj.file, f)
                
            if ext == '.json':
                # Handle JSON replacement
                with open(temp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise HTTPException(status_code=400, detail="JSON must contain a list of findings.")
                findings_db = data
                os.remove(temp_path)
                return {
                    "message": f"Successfully loaded {len(findings_db)} findings from {f_obj.filename}.",
                    "total_records": len(findings_db)
                }
                
            else:
                # Handle Document Ingestion via PII Scanner
                doc_input = ingest_file(temp_path)
                if not doc_input:
                    os.remove(temp_path)
                    continue
                
                # Use original filename for better tracking instead of the temp file hash
                doc_input.file_name = f_obj.filename
                
                # Run the AI/Regex scanner pipeline
                pipeline = FastFilterPipeline(state_manager=InMemoryStateManager())
                flagged_docs = pipeline.process_batch([doc_input])
                
                for flagged in flagged_docs:
                    for match in flagged.matches:
                        # Assign a basic risk level based on type
                        risk = "high" if match.pii_type.value in ["iban", "credit_card"] else "medium"
                        
                        finding = {
                            "finding_id": str(uuid.uuid4()),
                            "file_id": flagged.file_name,
                            "type": match.pii_type.value,
                            "value": match.matched_value,
                            "context": match.snippet,
                            "risk_level": risk,
                            "assigned_owner": "Unassigned",
                            "owner_resolved": False
                        }
                        findings_db.append(finding)
                        new_findings_count += 1
                        
                scanned_files += 1
                os.remove(temp_path)
                
        except Exception as e:
            # Skip failing files in a batch upload
            continue

    return {
        "message": f"Scanned {scanned_files} files. Found {new_findings_count} new PII matches.",
        "total_records": len(findings_db)
    }

