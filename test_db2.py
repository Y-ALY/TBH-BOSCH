from database import SessionLocal, Employee, FileMetadata
from sqlalchemy import or_, func, desc
db = SessionLocal()
results_raw = (
    db.query(
        Employee,
        func.count(FileMetadata.id).label("file_count"),
    )
    .outerjoin(FileMetadata, (FileMetadata.owner_employee_id == Employee.employee_id) & ~FileMetadata.file_path.startswith("[DELETED]"))
    .group_by(Employee.id)
    .order_by(desc("file_count"))
    .limit(5)
    .all()
)
for emp, count in results_raw:
    print(emp.employee_id, count)
