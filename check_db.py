import sqlite3

db = sqlite3.connect("bosch_gdpr.cache.db")
c = db.cursor()
c.execute("SELECT COUNT(*) FROM files")
print(f"Cache files count: {c.fetchone()[0]}")
db.close()

db = sqlite3.connect("bosch_gdpr.db")
c = db.cursor()
c.execute("SELECT COUNT(*) FROM files")
print(f"Main DB files count: {c.fetchone()[0]}")
db.close()
