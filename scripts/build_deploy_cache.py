#!/usr/bin/env python3
"""Scan strict_drive locally and write bosch_gdpr.cache.db for Render deploy.

Render cannot scan ~105k PDFs on free tier (OOM). Build the cache on your Mac, commit
bosch_gdpr.cache.db, redeploy — startup copies cache → live DB.

Usage (from repo root):
    python scripts/build_deploy_cache.py
    python scripts/build_deploy_cache.py --max-files 5000   # smaller DB for GitHub
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Allow imports from repo root when run as scripts/build_deploy_cache.py
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _remove_db_files() -> None:
    for name in (
        "bosch_gdpr.db",
        "bosch_gdpr.cache.db",
        "bosch_gdpr.db-wal",
        "bosch_gdpr.db-shm",
        "bosch_gdpr.cache.db-wal",
        "bosch_gdpr.cache.db-shm",
    ):
        p = ROOT / name
        if p.exists():
            p.unlink()
            print(f"Removed {p.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build bosch_gdpr.cache.db from strict_drive")
    parser.add_argument(
        "--folder",
        default="./strict_drive",
        help="Folder to scan (default: ./strict_drive)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit PDF count (0 = all). Use 3000–10000 if cache.db is too large for GitHub.",
    )
    parser.add_argument(
        "--ai-mode",
        default="off",
        choices=["off", "layered", "full"],
        help="AI mode during scan (off is fastest for cache build)",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"ERROR: folder not found: {folder}", file=sys.stderr)
        return 1

    pdf_count = sum(1 for _ in folder.rglob("*.pdf"))
    print(f"Found {pdf_count} PDFs under {folder}")
    if pdf_count == 0:
        return 1

    _remove_db_files()

    # Fresh schema + employee accounts (file/findings come from scan)
    from database import Base, engine, SessionLocal, ScanJob, FileMetadata, Finding

    Base.metadata.create_all(bind=engine)
    import seed_json_data

    seed_json_data.seed_data()

    scan_id = f"scan-cache-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    db.add(
        ScanJob(
            scan_id=scan_id,
            status="pending",
            options_json=json.dumps(
                {"mode": "full", "ai_mode": args.ai_mode, "strict_hash": False}
            ),
            created_at=datetime.now().isoformat(),
        )
    )
    db.commit()
    db.close()

    if args.max_files > 0:
        os.environ["SCAN_MAX_FILES"] = str(args.max_files)

    print(f"Scanning (ai_mode={args.ai_mode}, max_files={args.max_files or 'all'})...")
    from api import _run_background_scan

    _run_background_scan(
        scan_id,
        str(folder),
        "full",
        args.ai_mode,
        False,
    )

    src = ROOT / "bosch_gdpr.db"
    dst = ROOT / "bosch_gdpr.cache.db"
    shutil.copy2(src, dst)

    db = SessionLocal()
    files = db.query(FileMetadata).count()
    findings = db.query(Finding).count()
    db.close()

    size_mb = dst.stat().st_size / (1024 * 1024)
    print(f"\nDone: {dst.name} ({size_mb:.1f} MB)")
    print(f"  files={files}  findings={findings}")
    print("\nNext:")
    print("  git add -f bosch_gdpr.cache.db")
    print("  git commit -m 'Add prebuilt scan cache for Render'")
    print("  git push")
    print("  Redeploy on Render → Restart service")
    if size_mb > 90:
        print("\nWARNING: cache.db is large for GitHub (>100MB limit).")
        print("  Re-run with: python scripts/build_deploy_cache.py --max-files 5000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
