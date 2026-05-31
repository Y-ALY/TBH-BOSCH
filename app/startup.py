"""Startup seeding logic for the TBH-BOSCH FastAPI application.

Extracted from main.py's @app.on_event("startup") handler.
Keeps the database seeding behaviour identical: loads owner hints,
creates employee records, ingests FileMetadata from the scan root,
and cleans up files no longer on disk.
"""

import os

# Default scan root for the demo. Override with SCAN_ROOT env var.
_SCAN_ROOT = os.environ.get("SCAN_ROOT", "./demo_drive_rich")


def seed_on_startup(app):
    """Run database seeding and housekeeping at application startup.

    Call this from main.py's startup event handler.  The ``app``
    parameter is accepted for future use (e.g. storing state on
    app.state) but is not currently accessed.
    """
    import json
    import hashlib
    from pathlib import Path
    from datetime import datetime, timedelta
    from src.connector import LocalSampleRepoConnector

    from database import SessionLocal, Employee, FileMetadata, Finding

    db = SessionLocal()
    try:
        # We no longer clear the DB tables to preserve the delta state and
        # findings across reboots.  This mitigates the issue of files
        # appearing 'clean' initially.

        # 1. Load Owner Hints to generate Employees
        hints_path = Path(_SCAN_ROOT) / "owner_hints.json"
        hints = {}
        if hints_path.exists():
            with open(hints_path, "r", encoding="utf-8") as f:
                hints = json.load(f)

            # DEMO-ONLY: Hardcoded admin account with a well-known password.
            # In production: seed initial admin from env vars with a generated password.
        admin = db.query(Employee).filter(Employee.email == "admin@bosch.com").first()
        if not admin:
            admin = Employee(
                employee_id="BX-ADMIN", email="admin@bosch.com",
                first_name="Admin", last_name="User",
                password="password123", department="IT Security", location="Stuttgart"
            )
            db.add(admin)
            db.commit()

        # Cache existing employees to completely avoid N+1 queries
        all_employees = db.query(Employee).all()
        added_emails = {e.email for e in all_employees}
        added_emp_ids = {e.employee_id for e in all_employees}
        email_to_emp_id = {e.email: e.employee_id for e in all_employees}

        new_employees = []
        for file_name, hint in hints.items():
            email = hint.get("email", "unknown@bosch.com")
            if email in added_emails:
                continue

            emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
            emp_id_str = f"BX-{emp_id_int}"
            while emp_id_str in added_emp_ids:
                emp_id_int = (emp_id_int - 10000 + 1) % 90000 + 10000
                emp_id_str = f"BX-{emp_id_int}"

            added_emp_ids.add(emp_id_str)
            added_emails.add(email)
            email_to_emp_id[email] = emp_id_str

            first, last = hint.get("name", "Unknown User").split(" ", 1) if " " in hint.get("name", "") else (hint.get("name", "User"), "")
            emp = Employee(
                employee_id=emp_id_str, email=email,
                first_name=first, last_name=last,
                password="password123", department=hint.get("department", "Unknown"), location="Unknown"
            )
            new_employees.append(emp)
            db.add(emp)

        if new_employees:
            db.commit()

        # 2. Ingest FileMetadata from _SCAN_ROOT
        connector = LocalSampleRepoConnector(repo_path=_SCAN_ROOT)
        files = connector.list_files()

        # Cache existing file paths
        existing_file_paths = {f[0] for f in db.query(FileMetadata.file_path).all()}

        new_metas = []
        for idx, file_meta in enumerate(files):
            if file_meta.path in existing_file_paths:
                continue

            hint = hints.get(file_meta.file_name, {})
            email = hint.get("email")
            owner_id = email_to_emp_id.get(email, "BX-ADMIN") if email else "BX-ADMIN"

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
            new_metas.append(new_meta)
            db.add(new_meta)

        if new_metas:
            db.commit()

        # 3. Cleanup deleted files from the database to keep counts accurate
        all_db_files = db.query(FileMetadata).all()
        deleted_file_ids = []
        deleted_files = []
        for db_file in all_db_files:
            if not db_file.file_path.startswith("[DELETED]") and not os.path.exists(db_file.file_path):
                deleted_file_ids.append(db_file.id)
                deleted_files.append(db_file)

        if deleted_file_ids:
            db.query(Finding).filter(Finding.file_id.in_(deleted_file_ids)).delete(synchronize_session=False)
            for df in deleted_files:
                db.delete(df)
            db.commit()

        print(f"Successfully ingested {len(files)} files into FileMetadata on startup.")

    except Exception as e:
        print(f"Error injecting data on startup: {e}")
    finally:
        db.close()
