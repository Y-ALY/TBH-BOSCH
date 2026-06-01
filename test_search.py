from database import SessionLocal, Employee
db = SessionLocal()
from sqlalchemy import or_, func
q_lower = "%anna%"
try:
    res = db.query(Employee).filter(
        or_(
            func.lower(Employee.first_name).like(q_lower),
            func.lower(Employee.last_name).like(q_lower),
            func.lower(Employee.email).like(q_lower),
            func.lower(Employee.employee_id).like(q_lower),
            func.lower(Employee.first_name + " " + Employee.last_name).like(q_lower),
        )
    ).all()
    print("Success:", len(res))
except Exception as e:
    print("Error:", e)
