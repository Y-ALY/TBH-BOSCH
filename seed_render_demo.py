"""Populate SQLite with hackathon demo files/findings when cloud DB is empty."""

from __future__ import annotations

from datetime import datetime, timedelta

from database import SessionLocal, Employee, FileMetadata, Finding


def seed_render_demo() -> None:
    db = SessionLocal()
    try:
        if db.query(FileMetadata).count() > 0:
            return

        admin = db.query(Employee).filter(Employee.email == "admin@bosch.com").first()
        if not admin:
            admin = Employee(
                employee_id="BX-ADMIN",
                email="admin@bosch.com",
                first_name="System",
                last_name="Admin",
                password="password123",
                department="IT",
                location="Heilbronn",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
        elif admin.employee_id != "BX-ADMIN":
            admin.employee_id = "BX-ADMIN"
            db.commit()

        anna = db.query(Employee).filter(Employee.email == "anna1@bosch.com").first()
        if not anna:
            anna = Employee(
                employee_id="BX-21001",
                email="anna1@bosch.com",
                first_name="Anna",
                last_name="Keller",
                password="password123",
                department="Engineering",
                location="Stuttgart",
            )
            db.add(anna)
            db.commit()
            db.refresh(anna)

        now = datetime.now()
        demo_files = [
            (
                "/demo/wf_doc_001.txt",
                anna.employee_id,
                "anna1@bosch.com",
                "Passport Number",
                "L9923481X",
                "high",
            ),
            (
                "/demo/wf_doc_002.txt",
                anna.employee_id,
                "anna1@bosch.com",
                "Email Address",
                "anna1@bosch.com",
                "medium",
            ),
            (
                "/demo/hr_payroll_q4.pdf",
                admin.employee_id,
                "admin@bosch.com",
                "IBAN",
                "DE89370400440532013000",
                "high",
            ),
        ]

        for path, owner_id, owner_email, category, snippet, risk in demo_files:
            f = FileMetadata(
                file_path=path,
                owner_employee_id=owner_id,
                size_bytes=102400,
                last_modified=now,
                file_hash=f"demo-{path}",
                retention_deadline=now + timedelta(days=14),
            )
            db.add(f)
            db.flush()
            db.add(
                Finding(
                    file_id=f.id,
                    file_id_str=path,
                    category=category,
                    confidence_score=0.96,
                    flagged_snippet=snippet,
                    reasoning="Demo finding for cloud deploy",
                    status="pending_review",
                    review_status="pending_review",
                    type=category.lower().replace(" ", "_"),
                    value=snippet,
                    risk_level=risk,
                    owner_email=owner_email,
                    assigned_owner=owner_id,
                )
            )

        db.commit()
        print(
            f"Render demo seed OK: {len(demo_files)} files, "
            f"{db.query(Finding).count()} findings"
        )
    finally:
        db.close()
