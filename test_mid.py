import time
import os
import itertools
from src.connector import LocalSampleRepoConnector
from src.streaming_scanner import run_streaming_scan
from src.models import ScanOptions, FileScanResult

def main():
    conn = LocalSampleRepoConnector(repo_path="./strict_drive")
    file_refs = list(itertools.islice(conn.iter_files(), 20000))
    start_time = time.time()
    def silent_on_result(r): pass
    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", max_workers=os.cpu_count() or 4),
        on_result=silent_on_result
    )
    duration = time.time() - start_time
    print(f"Duration: {duration:.4f}")
    print(f"Metrics FPS: {metrics.files_per_second:.2f}")

if __name__ == "__main__":
    main()
