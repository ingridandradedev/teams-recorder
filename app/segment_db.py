import sqlite3
import threading
import time
from typing import List, Tuple

_lock = threading.Lock()

def init_db(db_path: str = ".segments.db"):
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY,
                seg_dir TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE,
                uploaded INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                gs_uri TEXT,
                last_error TEXT,
                uploaded_at REAL
            )
            """
        )
        conn.commit()
        conn.close()


def ensure_segment_record(seg_dir: str, filename: str, db_path: str = ".segments.db"):
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO segments(seg_dir, filename) VALUES (?,?)", (seg_dir, filename))
        conn.commit()
        conn.close()


def is_uploaded(seg_dir: str, filename: str, db_path: str = ".segments.db") -> bool:
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT uploaded FROM segments WHERE filename=?", (filename,))
        row = cur.fetchone()
        conn.close()
        return bool(row and row[0])


def increment_attempts(seg_dir: str, filename: str, db_path: str = ".segments.db") -> int:
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("UPDATE segments SET attempts = attempts + 1 WHERE filename=?", (filename,))
        conn.commit()
        cur.execute("SELECT attempts FROM segments WHERE filename=?", (filename,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else 0


def mark_uploaded(seg_dir: str, filename: str, gs_uri: str = None, db_path: str = ".segments.db"):
    now = time.time()
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "UPDATE segments SET uploaded=1, gs_uri=?, uploaded_at=? WHERE filename=?",
            (gs_uri, now, filename)
        )
        conn.commit()
        conn.close()


def set_last_error(seg_dir: str, filename: str, last_error: str, db_path: str = ".segments.db"):
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("UPDATE segments SET last_error=? WHERE filename=?", (last_error, filename))
        conn.commit()
        conn.close()


def get_unuploaded(seg_dir: str, db_path: str = ".segments.db") -> List[Tuple[str,int]]:
    with _lock:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT filename, attempts FROM segments WHERE seg_dir=? AND uploaded=0 ORDER BY id", (seg_dir,))
        rows = cur.fetchall()
        conn.close()
        return rows
