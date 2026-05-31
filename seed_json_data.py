import json
import re
import hashlib
from datetime import datetime
from database import SessionLocal, Employee, FileMetadata, Finding

def seed_data():
    db = SessionLocal()
    
    # Open the JSON file you uploaded
    try:
        with open('tests/test_cases/workflow_input_test_cases.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("Error: Make sure 'workflow_input_test_cases.json' is in the same folder!")
        return

    print("Parsing JSON and injecting into database...")

    for case in data['cases']:
        doc = case['document']
        expected = case['expected']
        content = doc['content']

        # 1. Extract the email dynamically
        email_match = re.search(r'[\w\.-]+@[\w\.-]+', content)
       # We add .rstrip('.') to remove the accidental sentence period
        email = email_match.group(0).lower().rstrip('.') if email_match else None

        # 2. Map the data to a specific persona
        first_name, last_name, dept, loc = "System", "Admin", "IT", "Heilbronn"
        
        if "Anna" in content:
            first_name, last_name, dept, loc = "Anna", "Keller", "Engineering", "Stuttgart"
        elif "Liam Weber" in content:
            first_name, last_name, dept, loc = "Liam", "Weber", "Customer Service", "Munich"
        elif "Noor Schmidt" in content:
            first_name, last_name, dept, loc = "Noor", "Schmidt", "HR", "Hamburg"
        elif "Marta Fischer" in content:
            first_name, last_name, dept, loc = "Marta", "Fischer", "Procurement", "Bremen"
        elif "Sofia Brandt" in content:
            first_name, last_name, dept, loc = "Sofia", "Brandt", "Finance", "Stuttgart"
        elif "Jonas Meyer" in content:
            first_name, last_name, dept, loc = "Jonas", "Meyer", "Sales", "Frankfurt"
        elif "Petra Lang" in content:
            first_name, last_name, dept, loc = "Petra", "Lang", "Legal", "Konstanz"
            if not email: email = "petra.lang@bosch.com" # Fallback since she only has a fax
        else:
            if not email: email = "admin@bosch.com"

        # 3. Create the Employee Account if it doesn't exist
        user = db.query(Employee).filter(Employee.email == email).first()
        if not user:
            # Generate a deterministic BX-ID based on their email
            emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
            emp_id = f"BX-{emp_id_int}"
            user = Employee(
                employee_id=emp_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password="password123", # Universal password for the demo
                department=dept,
                location=loc
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # 4. Insert the File into the Database
        file_meta = db.query(FileMetadata).filter(FileMetadata.file_path == doc['file_name']).first()
        if not file_meta:
            file_meta = FileMetadata(
                file_path=doc['file_name'],
                owner_employee_id=user.employee_id,
                size_bytes=len(content),
                file_hash=hashlib.md5(content.encode()).hexdigest(),
                last_modified=datetime.fromisoformat(doc['last_modified'])
            )
            db.add(file_meta)
            db.commit()
            db.refresh(file_meta)

        # 5. Populate the Dashboard with GDPR Findings (if expected to be flagged)
        if expected['should_flag']:
            for pii_type, count in expected.get('exact_match_counts', {}).items():
                finding = Finding(
                    file_id=file_meta.id,
                    category=pii_type.upper().replace("_", " "),
                    confidence_score=0.99,
                    flagged_snippet=f"[{pii_type} Match found in {doc['file_name']}]",
                    reasoning="Detected via Workflow JSON Rules",
                    status="Pending"
                )
                db.add(finding)
            db.commit()

    print("✅ Database successfully populated with all 50 documents and user accounts!")

if __name__ == "__main__":
    seed_data()