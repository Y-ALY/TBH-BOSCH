# Backend Agent Handoff Guide: New vs Old Architecture

This document is specifically written for AI agents working on this codebase to help distinguish between the legacy backend and the newly optimized high-throughput backend.

> [!IMPORTANT]
> When implementing new features or integrating the UI, **ALWAYS** use the New Streaming Backend architecture. Do not build on top of `run_full_scan`.

## 🔴 The Old Backend (Legacy / Deprecated)

**Key Function:** `run_full_scan` (and its wrapper `run_ai_scan`)
**Location:** `src/scanner.py` and `src/extractor.py` (`scan_directory`)

**How to identify it:**
- It aggregates all results by returning a single massive `ScanResult` object containing all parsed documents and findings.
- It iterates over files serially.
- It reads entire files into memory using standard file opening.
- **Current Status:** It is currently still wired into the FastAPI backend (`api.py` and `main.py`).

**Why it was replaced:**
It causes Out-Of-Memory (OOM) crashes and bottlenecking when attempting to process extremely large directories (e.g., the 100,000+ files in the `strict_drive` dataset). Throughput maxes out at ~150 files/sec.

## 🟢 The New Backend (Optimized / Current)

**Key Function:** `run_streaming_scan`
**Location:** `src/streaming_scanner.py`

**How to identify it:**
- It uses a **callback-driven** approach. Instead of returning a giant object, it invokes a callback parameter `on_result(file_result)` as soon as a single file finishes processing.
- It returns a `ScanMetrics` object upon completion, NOT a `ScanResult`.
- It utilizes `ProcessPoolExecutor` for multi-core concurrent processing.
- It utilizes memory-mapped files (`mmap`) to read chunks lazily without loading massive files into RAM.
- **Current Status:** Fully operational for benchmarks (`test_mmap_speed.py`) but **NOT** yet integrated into the UI endpoints (`api.py`/`main.py`).

**Why it is used:**
It maintains a completely flat memory profile and achieved a ~9x speedup in benchmarking (processing over 104,000 files in ~65 seconds at ~1,600 files/sec). 

## 🔄 Delta Scanning (Incremental Cache)

**Key Class:** `DeltaPlanner`
**Location:** `src/delta_planner.py` and `src/delta.py`

The new pipeline supports ultra-fast incremental scanning:
1. It saves file fingerprints (e.g., `file_id`, `size_bytes`, `last_modified`) to `latest.json` after a scan using `DeltaPlanner.save_state()`.
2. On the next run, `DeltaPlanner.plan()` streams the file references and does an instant metadata comparison to yield only the files that were modified, added, or deleted. 
3. This completely bypasses file I/O for unchanged files, dropping scan times for recall scans to milliseconds.

## Actionable Instructions for UI Integration

If you are asked to integrate the new backend into the UI (`api.py` or `main.py`), you must:
1. Replace calls to `run_full_scan`/`run_ai_scan` inside `_run_background_scan` with `run_streaming_scan`.
2. Provide a callback function (e.g., `def handle_result(result):`) to `run_streaming_scan` that immediately flushes the `FileScanResult` to the database using the existing `BulkWriter`.
3. Do not attempt to accumulate `FileScanResult` objects in a list, as this will recreate the OOM issue.
