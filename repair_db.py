import sqlite3
import hashlib

def repair_db(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # Get all non-admin employees
    c.execute("SELECT employee_id FROM employees WHERE employee_id NOT IN ('BX-17335', 'BX-35370')")
    emps = [row[0] for row in c.fetchall()]
    
    if not emps:
        print(f"No non-admin employees found in {db_path}!")
        return

    # Get all files
    c.execute("SELECT id, file_path FROM files")
    files = c.fetchall()
    
    updates = []
    for fid, fpath in files:
        h = int(hashlib.md5(fpath.encode()).hexdigest(), 16)
        owner = emps[h % len(emps)]
        updates.append((owner, fid))
        
    c.executemany("UPDATE files SET owner_employee_id = ? WHERE id = ?", updates)
    conn.commit()
    print(f"Updated {len(updates)} files in {db_path}.")
    conn.close()

if __name__ == "__main__":
    import os
    for db in ["bosch_gdpr.db", "bosch_gdpr.cache.db"]:
        if os.path.exists(db):
            repair_db(db)
