import sqlite3

def check():
    conn = sqlite3.connect("bosch_gdpr.db")
    c = conn.cursor()
    c.execute("SELECT employee_id, first_name, last_name, email FROM Employee")
    emps = c.fetchall()
    print("Employees:", len(emps))
    for e in emps:
        print(f"  {e[0]} - {e[1]} {e[2]} ({e[3]})")
        
    print("\nFile ownership counts:")
    c.execute("SELECT owner_employee_id, COUNT(*) FROM FileMetadata GROUP BY owner_employee_id")
    file_counts = c.fetchall()
    for row in file_counts:
        print(f"  Owner: {row[0]}, Count: {row[1]}")
        
if __name__ == "__main__":
    check()
