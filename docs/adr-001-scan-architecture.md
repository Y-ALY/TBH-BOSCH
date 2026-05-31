# ADR-001: Scan Architecture for Optimized GDPR Pipeline

**Status:** Accepted
**Date:** 2026-05-31
**Author:** Agent 0 (Shared Contracts Owner)

## Context

The existing GDPR scanning pipeline (`src/scanner.py`) uses a batch-oriented approach: it lists all files at once, downloads each completely into memory, and accumulates all results into global lists (`ScanResult.parsed_documents`, `ScanResult.findings`). This works for small repositories but will not scale to:

- Thousands of files (memory pressure from accumulating results)
- Large individual files (memory pressure from `download_file()` returning full `bytes`)
- Cloud-backed sources (OneDrive, SharePoint, Google Drive) where downloading every file is expensive
- Frequent re-scans where most files are unchanged

## Decision

### 1. Delta-First Scanning (metadata fingerprint before content hash)

Every `FileRef` carries an `etag_or_version` token derived from the source's native change tracking mechanism (mtime+size for local files, eTag for OneDrive, cTag for Google Drive, version for SharePoint). The delta layer compares these lightweight tokens against the previous scan's `DeltaState` before falling back to full content hashing.

- **Rationale:** Content hashing requires downloading the file. Metadata comparison avoids that for the vast majority of unchanged files. Only when metadata tokens differ (and `strict_hash=True`) do we compute a SHA-256 hash.

### 2. Streaming Pipeline (no global lists)

Data flows through the pipeline as iterators/streams, not as accumulated lists:

- `Connector.iter_files()` yields `FileRef` objects one at a time
- `Connector.open_file()` returns a `BinaryIO` stream instead of full bytes
- `FileScanResult` is emitted per-file, never collected into a global `list`
- `ScanMetrics` is updated incrementally as the scan progresses

- **Rationale:** Constant memory usage regardless of repository size. Enables early visibility into scan progress. Aligns with Python's iterator protocol.

### 3. AI as Layered/Async (not in hot path)

The `ScanOptions.ai_mode` field defaults to `"layered"`, meaning:

- Regex classification runs first (fast, deterministic, free)
- AI is invoked only for documents where regex is inconclusive (`document_type == "unknown"` or findings exist)
- AI results augment rather than replace regex results
- `"full"` mode runs AI on every document (for audits)
- `"off"` mode skips AI entirely (for quick scans)

- **Rationale:** AI API calls are the dominant cost and latency driver. Gatekeeping with regex avoids calling AI on confidently-classified clean documents. The layered approach gives the best cost/accuracy tradeoff.

### 4. Cloud Metadata-Before-Download

For cloud connectors, the `iter_files()` method retrieves only file metadata (name, size, modified date, etag) from the cloud API. Actual file content is only downloaded when:

- The file is new or modified (per delta check)
- `open_file()` is explicitly called

- **Rationale:** Cloud API calls for listing are cheap; downloading file contents is expensive in bandwidth, time, and API quota. This mirrors how cloud-native backup tools operate.

## Consequences

### Positive

- **Scalability:** Pipeline can handle arbitrary repository sizes with bounded memory.
- **Speed:** Delta mode skips unchanged files without downloading them.
- **Cost:** AI gatekeeping reduces API calls by 60-90% for typical corporate repositories.
- **Flexibility:** `ScanOptions` allows operators to choose the right tradeoff per scan.

### Negative / Tradeoffs

- **Complexity:** Streaming pipeline is harder to debug than a batch pipeline. Errors must be handled per-file (`FileScanError`) rather than via a single try/except.
- **Two file reference types:** `FileMetadata` (existing, with content hash) and `FileRef` (new, lightweight) coexist. This is intentional to preserve backward compatibility but creates a transitional period where both are in use.
- **State management:** Delta scanning requires persistent `DeltaState` between runs. A failed or interrupted scan leaves the state file stale.

## Compatibility

- `FileMetadata`, `ScanResult`, `Finding`, and all existing models remain unchanged.
- `list_files()` on all connectors continues to return `list[FileMetadata]`.
- `run_full_scan()` in `src/scanner.py` is unaffected.
- `api.py` is unaffected.
