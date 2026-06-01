import sys
import time
from sqlalchemy import create_engine, func, or_, desc
from sqlalchemy.orm import Session
from database import engine, Employee, FileMetadata, Finding

def old_query(q):
    db = Session(engine)
    q_lower = f"%{q.lower()}%"
    start = time.time()
    results_raw = (
        db.query(
            Employee,
            func.count(FileMetadata.id).label("file_count"),
        )
        .outerjoin(FileMetadata, (FileMetadata.owner_employee_id == Employee.employee_id) & ~FileMetadata.file_path.startswith("[DELETED]"))
        .filter(
            or_(
                func.lower(Employee.first_name).like(q_lower),
                func.lower(Employee.last_name).like(q_lower),
                func.lower(Employee.email).like(q_lower),
                func.lower(Employee.employee_id).like(q_lower),
                func.lower(Employee.first_name + " " + Employee.last_name).like(q_lower),
            )
        )
        .group_by(Employee.id)
        .order_by(desc("file_count"), Employee.last_name)
        .limit(50)
        .all()
    )
    
    results = []
    print(f"Old query raw took {time.time() - start:.2f}s")
    for emp, file_count in results_raw:
        loop_start = time.time()
        findings_count = 0
        if file_count > 0:
            findings_count = db.query(Finding).join(
                FileMetadata, Finding.file_id == FileMetadata.id
            ).filter(
                FileMetadata.owner_employee_id == emp.employee_id,
                ~FileMetadata.file_path.startswith("[DELETED]"),
                Finding.status != 'deleted',
                Finding.review_status != 'deleted'
            ).count()

        results.append({
            "employee_id": emp.employee_id,
            "findings_count": findings_count,
        })
        loop_time = time.time() - loop_start
        if loop_time > 0.1:
            print(f"Employee {emp.employee_id} took {loop_time:.2f}s")
    print(f"Old query total took {time.time() - start:.2f}s")
    return results

old_query("a")
