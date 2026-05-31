import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, FileMetadata, Finding, Employee

# 1. Connect to the SQLite Database
DATABASE_URL = "sqlite:///./bosch_gdpr.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

try:
    # 2. Create the physical file on disk
    file_dir = os.path.abspath("./demo_drive_rich")
    os.makedirs(file_dir, exist_ok=True)
    file_path = os.path.join(file_dir, "expired_gdpr_file.txt")
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("CONFIDENTIAL: Employee ID BX-1001, Visa Card Number: 4111-2222-3333-4444. This document contains expired PII.")
    
    print(f"[+] Physical file created at: {file_path}")
    
    # 3. Clean up any existing test records to avoid duplicates/conflicts
    existing_file = db.query(FileMetadata).filter(FileMetadata.file_path == file_path).first()
    if existing_file:
        db.query(Finding).filter(Finding.file_id == existing_file.id).delete()
        db.delete(existing_file)
        db.commit()
        print("[-] Cleaned up previous test records.")

    # 4. Insert FileMetadata with an expired retention deadline
    # Set retention deadline to 30 days in the past
    expired_deadline = datetime.now() - timedelta(days=30)
    
    test_file = FileMetadata(
        file_path=file_path,
        owner_employee_id="BX-1001",
        size_bytes=len("CONFIDENTIAL: Employee ID BX-1001, Visa Card Number: 4111-2222-3333-4444. This document contains expired PII."),
        file_hash="test_expired_hash_12345",
        last_modified=datetime.now(),
        retention_deadline=expired_deadline
    )
    db.add(test_file)
    db.commit()
    db.refresh(test_file)
    
    print(f"[+] DB FileMetadata created with ID: {test_file.id}")
    print(f"    - File Path: {test_file.file_path}")
    print(f"    - Retention Deadline: {test_file.retention_deadline} (Expired)")
    print(f"    - Owner Employee ID: {test_file.owner_employee_id}")

    # 5. Insert associated Finding record
    test_finding = Finding(
        file_id=test_file.id,
        finding_uid="finding-expired-test-999",
        file_id_str="expired_gdpr_file.txt",
        category="Credit Card Number",
        type="credit_card",
        value="4111-2222-3333-4444",
        flagged_snippet="Visa Card Number: 4111-2222-3333-4444",
        context="CONFIDENTIAL: Employee ID BX-1001, Visa Card Number: 4111-2222-3333-4444. This document contains expired PII.",
        risk_level="high",
        status="Pending",
        owner_resolved=False,
        assigned_owner="BX-1001"
    )
    db.add(test_finding)
    db.commit()
    db.refresh(test_finding)
    
    print(f"[+] DB Finding created with ID: {test_finding.id}")
    print(f"    - Status: {test_finding.status}")
    print("\n[SUCCESS] Setup complete! You can now test the API endpoint.")
    print("--------------------------------------------------")
    print(f"Target URL: POST http://localhost:8000/api/employee/files/{test_file.id}/delete-expired")
    print("Payload (JSON):")
    print('{\n  "employee_id": "BX-1001"\n}')
    print("--------------------------------------------------")

except Exception as e:
    db.rollback()
    print(f"[!] Error during database setup: {e}")
finally:
    db.close()
