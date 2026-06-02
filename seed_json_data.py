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
            # We are disabling this legacy dummy data seeding since we now use the real owner hints.
            # user = Employee(...)
            # db.add(user)
            # db.commit()
            # db.refresh(user)

        # 4. Insert the File into the Database (DISABLED - using real strict_drive data)
        # 5. Populate the Dashboard with GDPR Findings (DISABLED - using real strict_drive data)

    import os
    hints_path = "strict_drive/owner_hints.jsonl"
    if os.path.exists(hints_path):
        print("Parsing owner_hints.jsonl for all true employees...")
        existing_ids = {e.employee_id for e in db.query(Employee.employee_id).all()}
        unique_emails = {}
        with open(hints_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                email = data.get("email")
                username = data.get("username", "")
                if email and email not in unique_emails:
                    unique_emails[email] = username
        
        to_insert = []
        for email, username in unique_emails.items():
            emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
            emp_id = f"BX-{emp_id_int}"
            if emp_id not in existing_ids:
                parts = username.split(".") if "." in username else [username, ""]
                first_name = parts[0].capitalize()
                last_name = parts[1].capitalize() if len(parts) > 1 else ""
                # Strip numbers from last_name
                last_name = "".join([c for c in last_name if not c.isdigit()])
                
                to_insert.append({
                    "employee_id": emp_id,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "password": "password123",
                    "department": "Operations",
                    "location": "Global"
                })
                existing_ids.add(emp_id)
        
        if to_insert:
            print(f"Bulk inserting {len(to_insert)} newly discovered employees...")
            db.bulk_insert_mappings(Employee, to_insert)
            db.commit()

    print("✅ Database successfully populated with user accounts! (Dummy file seeding disabled)")
if __name__ == "__main__":
    seed_data()