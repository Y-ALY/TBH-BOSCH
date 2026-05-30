import os
import hashlib
from datetime import datetime
from sqlalchemy.orm import Session
from database import FileMetadata

def calculate_file_hash(filepath: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            buf = f.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = f.read(65536)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

def run_delta_scan(target_directory: str, db: Session):
    print(f"--- Starting Delta Scan on: {target_directory} ---")
    files_scanned = 0
    files_skipped = 0

    for root, dirs, files in os.walk(target_directory):
        for file in files:
            file_path = os.path.join(root, file)
            
            # 1. Get OS-level metadata (Lightning fast)
            current_size = os.path.getsize(file_path)
            # Convert OS timestamp to Python datetime for the DB
            current_mtime = datetime.fromtimestamp(os.path.getmtime(file_path)) 

            existing_file = db.query(FileMetadata).filter(FileMetadata.file_path == file_path).first()

            if existing_file:
                # 🚀 THE PERFORMANCE UPGRADE: The Metadata Pre-Check
                # If the size is the same AND it hasn't been modified since we last checked, skip instantly!
                # We do NOT calculate the heavy MD5 hash.
                if existing_file.size_bytes == current_size and existing_file.last_modified >= current_mtime:
                    files_skipped += 1
                    continue 
                
                # If the metadata changed, NOW we do the heavy read to verify contents
                current_hash = calculate_file_hash(file_path)
                if not current_hash: continue

                if existing_file.file_hash == current_hash:
                    files_skipped += 1
                    continue
                else:
                    print(f"[UPDATE] File changed: {file}")
                    existing_file.file_hash = current_hash
                    existing_file.size_bytes = current_size
                    existing_file.last_modified = current_mtime
                    files_scanned += 1
            else:
                print(f"[NEW] File discovered: {file}")
                current_hash = calculate_file_hash(file_path)
                if not current_hash: continue
                
                new_file = FileMetadata(
                    file_path=file_path,
                    size_bytes=current_size,
                    file_hash=current_hash,
                    last_modified=current_mtime,
                    owner_employee_id="BX-21842" # Tying to the hardcoded ID in the HTML
                )
                db.add(new_file)
                files_scanned += 1

    db.commit()
    print(f"--- Scan Complete! Sent to AI: {files_scanned} | Fast Skipped: {files_skipped} ---")
    return {"scanned": files_scanned, "skipped": files_skipped}