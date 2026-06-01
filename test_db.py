from database import SessionLocal, Employee, FileMetadata
db = SessionLocal()
emp = db.query(Employee).first()
print("Employee ID:", emp.employee_id)
f = db.query(FileMetadata).first()
print("File owner ID:", f.owner_employee_id if f else "No files")
