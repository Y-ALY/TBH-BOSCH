import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import hashlib
import json

from database import SessionLocal, Employee, FileMetadata, Finding
from src.connector import LocalSampleRepoConnector

db = SessionLocal()
try:
    hints_path = Path("demo_drive_rich/owner_hints.json")
    hints = {}
    if hints_path.exists():
        with open(hints_path, "r", encoding="utf-8") as f:
            hints = json.load(f)
            
    connector = LocalSampleRepoConnector(repo_path="./demo_drive_rich")
    files = connector.list_files()
    print(f"Found {len(files)} files in connector.")
    
    added = 0
    for idx, file_meta in enumerate(files):
        existing = db.query(FileMetadata).filter(FileMetadata.file_path == file_meta.path).first()
        if not existing:
            hint = hints.get(file_meta.file_name, {})
            email = hint.get("email")
            owner_id = "BX-ADMIN"
            if email:
                emp = db.query(Employee).filter(Employee.email == email).first()
                if emp:
                    owner_id = emp.employee_id
            
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
            db.add(new_meta)
            added += 1
            if added % 100 == 0:
                print(f"Added {added} files...")
                db.commit()
    db.commit()
    print(f"Successfully added {added} new files.")
except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
