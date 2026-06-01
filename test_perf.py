import time
from database import SessionLocal, Employee, FileMetadata, Finding
from sqlalchemy import or_, func, desc
from sqlalchemy.orm import Session

db = SessionLocal()
q_lower = "%a%"
start = time.time()
matching_emps_query = (
    db.query(Employee, func.count(FileMetadata.id).label("file_count"))
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
print("Query took:", time.time() - start)

emp_ids = [emp.employee_id for emp, _ in matching_emps_query]
if emp_ids:
    start = time.time()
    findings_counts = (
        db.query(
            FileMetadata.owner_employee_id,
            func.count(Finding.id)
        )
        .join(Finding, Finding.file_id == FileMetadata.id)
        .filter(
            FileMetadata.owner_employee_id.in_(emp_ids),
            ~FileMetadata.file_path.startswith("[DELETED]"),
            Finding.status != 'deleted',
            Finding.review_status != 'deleted'
        )
        .group_by(FileMetadata.owner_employee_id)
        .all()
    )
    print("Findings query took:", time.time() - start)
