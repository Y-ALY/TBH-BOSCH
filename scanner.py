import os
import hashlib
from datetime import datetime
from sqlalchemy.orm import Session
from database import FileMetadata

# We use MD5 for speed. It's not for security, just for detecting changes fast.
def calculate_file_hash(filepath: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            buf = f.read(65536) # Read in 64kb chunks to handle large files safely
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

    # Walk through every folder and file in the target directory
    for root, dirs, files in os.walk(target_directory):
        for file in files:
            file_path = os.path.join(root, file)
            
            # 1. Get current file metadata
            current_size = os.path.getsize(file_path)
            current_hash = calculate_file_hash(file_path)
            
            if not current_hash:
                continue # Skip unreadable files

            # 2. Check the database to see if we already know this file
            existing_file = db.query(FileMetadata).filter(FileMetadata.file_path == file_path).first()

            if existing_file:
                # Delta Check: Did the hash change?
                if existing_file.file_hash == current_hash:
                    files_skipped += 1
                    continue # FAST SKIP! The file hasn't changed.
                else:
                    print(f"[UPDATE] File changed, queuing for AI Scan: {file}")
                    # Update the DB with the new hash
                    existing_file.file_hash = current_hash
                    existing_file.size_bytes = current_size
                    existing_file.last_modified = datetime.now()
                    files_scanned += 1
                    
                    # TODO: Pass `file_path` to Team A's AI function here!
            else:
                print(f"[NEW] File discovered, queuing for AI Scan: {file}")
                # This is a brand new file, log it in the DB
                new_file = FileMetadata(
                    file_path=file_path,
                    size_bytes=current_size,
                    file_hash=current_hash,
                    last_modified=datetime.now(),
                    owner_employee_id="Unknown" # Person 4 will simulate this later
                )
                db.add(new_file)
                files_scanned += 1
                
                # TODO: Pass `file_path` to Team A's AI function here!

    # Commit all new/updated files to the database
    db.commit()
    print(f"--- Scan Complete! Scanned (Sent to AI): {files_scanned} | Skipped (Unchanged): {files_skipped} ---")
    return {"scanned": files_scanned, "skipped": files_skipped}