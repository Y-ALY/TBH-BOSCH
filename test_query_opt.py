import sys
from sqlalchemy import create_engine, func, or_, desc
from sqlalchemy.orm import Session
from database import engine, Employee, FileMetadata, Finding

def test_query(q):
    db = Session(engine)
    q_lower = f"%{q.lower()}%"
    
    findings_subq = (
        db.query(
            FileMetadata.owner_employee_id.label("employee_id"),
            func.count(Finding.id).label("findings_count")
        )
        .join(Finding, Finding.file_id == FileMetadata.id)
        .filter(
            ~FileMetadata.file_path.startswith("[DELETED]"),
            Finding.status != 'deleted',
            Finding.review_status != 'deleted'
        )
        .group_by(FileMetadata.owner_employee_id)
        .subquery()
    )

    results_raw = (
        db.query(
            Employee,
            func.count(FileMetadata.id).label("file_count"),
            func.coalesce(findings_subq.c.findings_count, 0).label("findings_count")
        )
        .outerjoin(FileMetadata, (FileMetadata.owner_employee_id == Employee.employee_id) & ~FileMetadata.file_path.startswith("[DELETED]"))
        .outerjoin(findings_subq, findings_subq.c.employee_id == Employee.employee_id)
        .filter(
            or_(
                func.lower(Employee.first_name).like(q_lower),
                func.lower(Employee.last_name).like(q_lower),
                func.lower(Employee.email).like(q_lower),
                func.lower(Employee.employee_id).like(q_lower),
                func.lower(Employee.first_name + " " + Employee.last_name).like(q_lower),
            )
        )
        .group_by(Employee.id, findings_subq.c.findings_count)
        .order_by(desc("file_count"), Employee.last_name)
        .limit(50)
        .all()
    )
    
    for emp, file_count, findings_count in results_raw:
        print(emp.employee_id, emp.first_name, file_count, findings_count)

test_query("a")
