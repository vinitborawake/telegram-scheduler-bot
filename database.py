import sqlite3
from datetime import datetime
from typing import Optional


DB_FILE = "scheduled_posts.db"


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the scheduled_posts table if it doesn't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_file_id TEXT NOT NULL,
            caption TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def add_post(image_file_id: str, caption: str, scheduled_time: datetime) -> int:
    """Add a new scheduled post. Returns the post ID."""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO scheduled_posts (image_file_id, caption, scheduled_time) VALUES (?, ?, ?)",
        (image_file_id, caption, scheduled_time.isoformat()),
    )
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return post_id


def get_pending_posts() -> list[dict]:
    """Get all posts with status 'pending'."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM scheduled_posts WHERE status = 'pending' ORDER BY scheduled_time ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_post(post_id: int) -> Optional[dict]:
    """Get a single post by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_posted(post_id: int):
    """Mark a post as 'posted'."""
    conn = get_connection()
    conn.execute(
        "UPDATE scheduled_posts SET status = 'posted' WHERE id = ?", (post_id,)
    )
    conn.commit()
    conn.close()


def cancel_post(post_id: int) -> bool:
    """Cancel a pending post. Returns True if a row was updated."""
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE scheduled_posts SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
        (post_id,),
    )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated
