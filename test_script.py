import sqlite3
import json
from datetime import datetime

conn = sqlite3.connect('bosch_gdpr.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT * FROM employees WHERE first_name='Bettina' OR last_name='Patberg'")
emp = cur.fetchone()
if not emp:
    print('Employee not found')
    exit()

print(f"Employee: {emp['employee_id']} {emp['first_name']} {emp['last_name']}")

cur.execute("SELECT * FROM files WHERE owner_employee_id=? AND file_path NOT LIKE '[DELETED]%'", (emp['employee_id'],))
files = cur.fetchall()
print(f"Total Active Files: {len(files)}")

now = datetime.now()

for f in files:
    fid = f['id']
    deadline = f['retention_deadline']
    path = f['file_path']
    is_expired = False
    if deadline:
        is_expired = str(deadline) < str(now)
    
    cur.execute("SELECT status, review_status FROM findings WHERE file_id=? AND status != 'deleted'", (fid,))
    findings = cur.fetchall()
    
    print(f"File {fid}: {path}")
    print(f"  Deadline: {deadline} (Expired: {is_expired})")
    print(f"  Findings: {len(findings)}")
