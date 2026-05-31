#!/usr/bin/env python3
"""Deterministic demo-data seeder for the TBH-BOSCH presentation deployment.

Usage:
    python scripts/demo/seed_demo_data.py

What it does:
  1. Creates demo user accounts (admin + employees from owner_hints.json)
  2. Injects FileMetadata rows for files in the demo directory
  3. Creates preseeded findings so the dashboard shows data immediately
  4. Idempotent — skips rows that already exist

Environment:
    DATABASE_URL  – PostgreSQL connection string (defaults to local SQLite)
    SCAN_ROOT     – path to demo directory (defaults to ./demo_drive_rich)

Credentials (DEMO-ONLY — NOT for production):
    admin@bosch.com     / password123
    All employee accounts / password123
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the repo root is on sys.path so we can import the app modules
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ── Config from environment ──────────────────────────────────────────────────
SCAN_ROOT = Path(os.environ.get("SCAN_ROOT", REPO_ROOT / "demo_drive_rich"))
HINTS_PATH = SCAN_ROOT / "owner_hints.json"

# ── Database setup ───────────────────────────────────────────────────────────
from database import SessionLocal, Base, engine, Employee, FileMetadata, Finding

# Ensure tables exist (safe to call multiple times)
Base.metadata.create_all(bind=engine)


def _load_hints() -> dict:
    """Load owner_hints.json, returning {} if not found."""
    if not HINTS_PATH.exists():
        print(f"[WARN] owner_hints.json not found at {HINTS_PATH} — skipping employee seed.")
        return {}
    with open(HINTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_employees(db, hints: dict) -> list[Employee]:
    """Create demo employees from hints. Returns list of all Employee objects."""
    employees: list[Employee] = []

    # Always ensure admin exists
    admin = db.query(Employee).filter(Employee.email == "admin@bosch.com").first()
    if not admin:
        admin = Employee(
            employee_id="BX-ADMIN",
            email="admin@bosch.com",
            first_name="Admin",
            last_name="User",
            password="password123",   # ⚠️ DEMO-ONLY plaintext
            department="IT Security",
            location="Stuttgart",
        )
        db.add(admin)
        employees.append(admin)
        print("[SEED] Created admin user: admin@bosch.com / password123")
    else:
        employees.append(admin)

    # Collect existing emails to avoid duplicates
    existing = db.query(Employee).all()
    existing_emails = {e.email for e in existing}
    existing_ids = {e.employee_id for e in existing}

    for file_name, hint in hints.items():
        email = hint.get("email", "unknown@bosch.com")
        if email in existing_emails:
            continue

        emp_id_int = int(hashlib.md5(email.encode()).hexdigest(), 16) % 90000 + 10000
        emp_id = f"BX-{emp_id_int}"
        while emp_id in existing_ids:
            emp_id_int = (emp_id_int - 10000 + 1) % 90000 + 10000
            emp_id = f"BX-{emp_id_int}"
        existing_ids.add(emp_id)
        existing_emails.add(email)

        name_parts = hint.get("name", "Unknown User").split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        emp = Employee(
            employee_id=emp_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password="password123",   # ⚠️ DEMO-ONLY plaintext
            department=hint.get("department", "Unknown"),
            location="Unknown",
        )
        db.add(emp)
        employees.append(emp)

    db.commit()
    print(f"[SEED] Employees: {len(existing)} existing, {len(employees) - len(existing) if len(employees) > len(existing) else 0} new")
    return employees


def seed_files(db, hints: dict, email_to_emp_id: dict):
    """Create FileMetadata rows for files in SCAN_ROOT."""
    if not SCAN_ROOT.exists():
        print(f"[SKIP] SCAN_ROOT {SCAN_ROOT} does not exist — skipping file seed.")
        return

    existing_paths = {r[0] for r in db.query(FileMetadata.file_path).all()}

    new_count = 0
    for idx, entry in enumerate(sorted(SCAN_ROOT.iterdir())):
        if entry.is_dir() or not entry.suffix.lower() == ".pdf":
            continue
        # Use absolute path to match the format from LocalSampleRepoConnector
        abs_path = str(entry.resolve())
        if abs_path in existing_paths:
            continue

        hint = hints.get(entry.name, {})
        email = hint.get("email")
        owner_id = email_to_emp_id.get(email, "BX-ADMIN") if email else "BX-ADMIN"

        stat = entry.stat()
        days_offset = -10 if idx % 3 == 0 else (15 if idx % 3 == 1 else 200)

        f = FileMetadata(
            file_path=abs_path,
            owner_employee_id=owner_id,
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            file_hash=hashlib.sha256(entry.read_bytes()).hexdigest(),
            retention_deadline=datetime.now() + timedelta(days=days_offset),
        )
        db.add(f)
        new_count += 1

    db.commit()
    print(f"[SEED] Files: {len(existing_paths)} existing, {new_count} new")


def seed_findings(db):
    """Create preseeded demo findings for files that have none."""
    # Get all files and their current finding counts
    files = db.query(FileMetadata).all()
    if not files:
        print("[SKIP] No files in DB — seed files first.")
        return

    existing_finding_files = {
        r[0] for r in db.query(Finding.file_id).filter(Finding.file_id.isnot(None)).distinct().all()
    }

    # Preseeded finding templates — one per category for variety
    templates = [
        {"type": "email", "risk_level": "high", "action": "delete", "value_tpl": "{name}@company.com"},
        {"type": "phone", "risk_level": "medium", "action": "mask", "value_tpl": "+49-{num}-{tail}"},
        {"type": "iban", "risk_level": "high", "action": "escalate_dpo", "value_tpl": "DE{num}{tail}"},
        {"type": "tax_id", "risk_level": "medium", "action": "review", "value_tpl": "DE{num}"},
        {"type": "passport", "risk_level": "high", "action": "delete", "value_tpl": "L{num}X"},
        {"type": "credit_card", "risk_level": "high", "action": "escalate_dpo", "value_tpl": "{num4}-{num4}-{num4}-{num4}"},
        {"type": "address", "risk_level": "low", "action": "review", "value_tpl": "Sample Str. {num}, Berlin"},
        {"type": "name", "risk_level": "low", "action": "retain", "value_tpl": "Employee {num}"},
    ]

    new_count = 0
    for f in files:
        if f.id in existing_finding_files:
            continue

        # Assign 1-3 findings per file, cycling through templates
        import random
        rng = random.Random(hash(f.file_path))
        num_findings = rng.randint(1, 3)

        for i in range(num_findings):
            t = templates[(hash(f.file_path) + i) % len(templates)]
            num = str(rng.randint(100, 99999))
            tail = str(rng.randint(1000, 9999))
            num4 = str(rng.randint(1000, 9999))

            value = t["value_tpl"].format(
                name=f.file_path.split("/")[-1].split("_")[1].title() if "_" in f.file_path else "User",
                num=num, tail=tail, num4=num4,
            )

            finding = Finding(
                file_id=f.id,
                file_id_str=f.file_path,
                type=t["type"],
                value=value,
                context=f"Detected in {f.file_path}",
                risk_level=t["risk_level"],
                confidence=round(rng.uniform(0.75, 0.99), 2),
                recommended_action=t["action"],
                assigned_owner=f.owner_employee_id or "BX-ADMIN",
                is_flagged=True,
                flag_type="Demo_Seed",
                status="pending_review",
                review_status="pending_review",
                # Legacy columns
                category=t["type"],
                confidence_score=round(rng.uniform(0.75, 0.99), 2),
                flagged_snippet=value,
                reasoning=f"Preseeded demo finding in {f.file_path}",
            )
            db.add(finding)
            new_count += 1

    db.commit()
    print(f"[SEED] Findings: {len(existing_finding_files)} files already had findings, {new_count} new findings created")


def _resolve_owners(db):
    """Resolve email→employee_id map from owner_hints or existing employees."""
    hints = _load_hints()
    email_to_emp_id: dict = {}
    employees = db.query(Employee).all()
    for emp in employees:
        email_to_emp_id[emp.email] = emp.employee_id

    return hints, email_to_emp_id


def main():
    db = SessionLocal()
    try:
        print("=" * 60)
        print("  TBH-BOSCH Demo Data Seeder")
        print(f"  SCAN_ROOT:     {SCAN_ROOT}")
        print(f"  DATABASE_URL:  {os.environ.get('DATABASE_URL', 'sqlite:///./bosch_gdpr.db')}")
        print("=" * 60)

        hints, email_to_emp_id = _resolve_owners(db)

        # 1. Seed employees
        seed_employees(db, hints)

        # Re-resolve after employee seed
        hints, email_to_emp_id = _resolve_owners(db)

        # 2. Seed files
        seed_files(db, hints, email_to_emp_id)

        # 3. Seed findings (preseeded demo data)
        seed_findings(db)

        print("=" * 60)
        print("  Seed complete.")
        print("  Default credentials: admin@bosch.com / password123")
        print("  Employee passwords:  password123")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
