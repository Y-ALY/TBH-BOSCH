import os
for ext in ['-wal', '-shm']:
    f = f"bosch_gdpr.db{ext}"
    if os.path.exists(f):
        os.remove(f)
