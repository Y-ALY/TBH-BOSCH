import time
import os
import json
from src.connector import LocalSampleRepoConnector
from src.streaming_scanner import run_streaming_scan
from src.models import ScanOptions, FileScanResult

def main():
    print("Initializing benchmark...")
    conn = LocalSampleRepoConnector(repo_path="./strict_drive")
    
    file_refs = conn.iter_files()
    print("Streaming files directly for benchmarking...")
    
    # Run the mmap/multicore scanner
    start_time = time.time()
    
    # Suppress output to accurately measure processing speed, not terminal IO
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
