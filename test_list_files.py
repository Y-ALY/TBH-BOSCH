import time
from src.connector import LocalSampleRepoConnector

conn = LocalSampleRepoConnector(repo_path="./strict_drive")
start_time = time.time()
file_refs = list(conn.iter_files())
duration = time.time() - start_time
print(f"Listed {len(file_refs)} files in {duration:.4f} seconds.")
