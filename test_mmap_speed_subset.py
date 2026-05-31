import time
import os
from src.connector import LocalSampleRepoConnector
from src.streaming_scanner import run_streaming_scan
from src.models import ScanOptions, FileScanResult

def main():
    print("Initializing benchmark subset...")
    conn = LocalSampleRepoConnector(repo_path="./strict_drive")
    
    import itertools
    file_refs = list(itertools.islice(conn.iter_files(), 2000))
    print(f"Discovered {len(file_refs)} files for benchmarking.")
    
    start_time = time.time()
    
    def silent_on_result(r: FileScanResult):
        pass
        
    metrics = run_streaming_scan(
        conn,
        iter(file_refs),
        ScanOptions(mode="full", max_workers=os.cpu_count() or 4),
        on_result=silent_on_result
    )
    
    duration = time.time() - start_time
    print("\n--- RUNTIME ANALYSIS ---")
    print(f"Total files scanned: {metrics.files_scanned}")
    print(f"Total time elapsed:  {duration:.4f} seconds")
    print(f"Throughput:          {metrics.files_per_second:.2f} files/second")
    print(f"Findings discovered: {metrics.total_findings}")
    print("------------------------")

if __name__ == "__main__":
    main()
