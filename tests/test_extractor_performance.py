"""Performance guardrails for the admin intake extraction path."""

from __future__ import annotations

import time

from src.extractor import scan_directory


def test_one_mb_text_file_scans_under_five_seconds(tmp_path):
    source_file = tmp_path / "one_mb_employee_dump.txt"
    line = (
        "Employee: Alex Meyer | Email: alex.meyer@bosch.com | "
        "Phone: +49 170 1234567 | Tax ID: DE123456789 | "
        "Notes: internal project record.\n"
    )

    with source_file.open("w", encoding="utf-8") as f:
        while f.tell() < 1_048_576:
            f.write(line)

    started = time.perf_counter()
    result = scan_directory(str(tmp_path))
    elapsed_seconds = time.perf_counter() - started

    assert source_file.stat().st_size >= 1_048_576
    assert result["admin_aggregates"]["total_scanned_files"] == 1
    assert result["admin_aggregates"]["total_findings"] >= 3
    assert elapsed_seconds < 5.0
