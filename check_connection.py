"""Quick health check: is an external source connected, and did its data land?

Run:  venv/Scripts/python.exe check_connection.py
"""
import sqlite3

db = sqlite3.connect("bosch_gdpr.db")
c = db.cursor()

print("=== ACTIVE CONNECTION ===")
rows = c.execute(
    "SELECT source_type, connection_config, created_at FROM active_connection"
).fetchall()
if not rows:
    print("  (none) — currently treated as local")
for source_type, cfg, created in rows:
    print(f"  source_type = {source_type}")
    print(f"  config      = {cfg}")
    print(f"  connected_at= {created}")

print("\n=== LAST 5 SCAN JOBS ===")
for row in c.execute(
    "SELECT scan_id, status, total_files, total_findings, error_message, created_at "
    "FROM scan_jobs ORDER BY id DESC LIMIT 5"
):
    print(" ", row)

print("\n=== TOTALS ===")
print("  files    :", c.execute("SELECT COUNT(*) FROM files").fetchone()[0])
print("  findings :", c.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
print("  employees:", c.execute("SELECT COUNT(*) FROM employees").fetchone()[0])

print("\n=== GOOGLE DRIVE / EXTERNAL IMPORT ===")
ext = c.execute(
    "SELECT COUNT(*) FROM findings WHERE finding_uid LIKE 'ext-%'"
).fetchone()[0]
print(f"  appended findings (ext-*): {ext}")
if ext:
    print("  sample:")
    for row in c.execute(
        "SELECT value, owner_department, risk_level FROM findings "
        "WHERE finding_uid LIKE 'ext-%' LIMIT 5"
    ):
        print("   ", row)
    print("  => Google Drive import IS present in the database.")
else:
    print("  => No external import rows found.")

db.close()
