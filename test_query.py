from database import SessionLocal, Employee, FileMetadata
db = SessionLocal()
from sqlalchemy import or_, func, desc
q_lower = "%anna%"
try:
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
    print("Success! Got", len(results_raw), "results.")
except Exception as e:
    print("Error:", e)
