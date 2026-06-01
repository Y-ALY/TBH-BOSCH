import sqlite3
from datetime import datetime

def fix_keudelerhard():
    conn = sqlite3.connect('bosch_gdpr.db')
    cursor = conn.cursor()
    
    # 1. Find keudelerhard
    cursor.execute("SELECT id, employee_id FROM employees WHERE email LIKE '%keudelerhard%'")
    user = cursor.fetchone()
    if not user:
        print("User not found!")
        return
        
    emp_id = user[1]
    
    # 2. Find expired files
    now_str = str(datetime.now())
    cursor.execute("SELECT id, file_path FROM files WHERE owner_employee_id=? AND retention_deadline < ?", (emp_id, now_str))
    expired_files = cursor.fetchall()
    
    print(f"Found {len(expired_files)} expired files for keudelerhard.")
    
    for f in expired_files:
        fid = f[0]
        fpath = f[1]
        
        # Check if finding already exists
        cursor.execute("SELECT id FROM findings WHERE file_id=?", (fid,))
        if cursor.fetchone():
            continue
            
        category = "MEDICAL RECORD" if "medical" in fpath else "PASSPORT"
            
        cursor.execute("""
            INSERT INTO findings (
                file_id, category, confidence_score, flagged_snippet, reasoning, status, 
                review_status, type, value, risk_level, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fid, category, 0.99, f"[PII Detected in {fpath.split('/')[-1]}]", 
            "Detected by Compliance Engine", "pending_review", "pending", 
            "pii", "REDACTED", "high", 0.99
        ))
        
    conn.commit()
    conn.close()
    print("Done inserting findings!")

if __name__ == '__main__':
    fix_keudelerhard()
