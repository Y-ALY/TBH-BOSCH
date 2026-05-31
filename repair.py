import json
from pathlib import Path
from database import SessionLocal, Employee, FileMetadata

def repair_db():
    db = SessionLocal()
    
    # 1. Load hints
    with open("demo_drive_rich/owner_hints.json", "r") as f:
        hints = json.load(f)
        
    # 2. Get email to employee_id mapping
    employees = db.query(Employee).all()
    email_to_emp_id = {e.email: e.employee_id for e in employees}
    
    # 3. Update FileMetadata
    files = db.query(FileMetadata).all()
    updated = 0
    for f in files:
        # f.file_path is like /Users/.../demo_drive_rich/filename.pdf
        filename = Path(f.file_path).name
        hint = hints.get(filename, {})
        email = hint.get("email")
        if email and email in email_to_emp_id:
            correct_owner = email_to_emp_id[email]
            if f.owner_employee_id != correct_owner:
                f.owner_employee_id = correct_owner
                updated += 1
                
    db.commit()
    print(f"Successfully repaired {updated} file ownerships.")
    db.close()

if __name__ == "__main__":
    repair_db()
