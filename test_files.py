from database import SessionLocal, FileMetadata, Employee
db = SessionLocal()
files = db.query(FileMetadata).all()
print("Total files:", len(files))
emp_with_files = db.query(FileMetadata.owner_employee_id).distinct().all()
print("Unique owners:", emp_with_files)
