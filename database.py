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
    """Create tables if they don't exist and run necessary migrations."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_file_id TEXT,
            media_type TEXT NOT NULL DEFAULT 'photo',
            caption TEXT NOT NULL,
            caption_entities TEXT,
            scheduled_time TEXT NOT NULL,
            source_chat_id INTEGER,
            source_message_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Migrations: ensure columns exist in scheduled_posts
    cursor = conn.execute("PRAGMA table_info(scheduled_posts)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "caption_entities" not in columns:
        try:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN caption_entities TEXT")
        except Exception:
            pass
    if "media_type" not in columns:
        try:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'")
        except Exception:
            pass
    if "media_file_id" not in columns:
        try:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN media_file_id TEXT")
        except Exception:
            pass
    if "source_chat_id" not in columns:
        try:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN source_chat_id INTEGER")
        except Exception:
            pass
    if "source_message_id" not in columns:
        try:
            conn.execute("ALTER TABLE scheduled_posts ADD COLUMN source_message_id INTEGER")
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve a setting by key."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    """Set or update a setting by key."""
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


DEFAULT_RATE_LIMIT = 300  # 5 minutes default gap


def get_rate_limit() -> int:
    """Get the rate limit in seconds (default: 300 seconds = 5 minutes)."""
    val = get_setting("rate_limit_seconds", str(DEFAULT_RATE_LIMIT))
    try:
        return max(10, int(val))
    except ValueError:
        return DEFAULT_RATE_LIMIT



def set_rate_limit(seconds: int):
    """Set the rate limit in seconds."""
    set_setting("rate_limit_seconds", str(max(10, seconds)))



def add_post(
    caption: str,
    scheduled_time: datetime,
    media_file_id: str = None,
    media_type: str = "none",
    caption_entities: str = None,
    source_chat_id: int = None,
    source_message_id: int = None,
) -> int:
    """Add a new scheduled post. Returns the post ID.
    media_type can be: 'photo', 'video', or 'none'
    caption_entities is a JSON string of Telegram MessageEntity objects
    source_chat_id and source_message_id allow copying the exact message (preserves premium emojis)
    """
    conn = get_connection()
    cursor = conn.execute("PRAGMA table_info(scheduled_posts)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "image_file_id" in columns:
        cursor = conn.execute(
            """INSERT INTO scheduled_posts 
               (image_file_id, media_file_id, media_type, caption, caption_entities, scheduled_time, source_chat_id, source_message_id) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (media_file_id or "", media_file_id, media_type, caption, caption_entities, scheduled_time.isoformat(), source_chat_id, source_message_id),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO scheduled_posts 
               (media_file_id, media_type, caption, caption_entities, scheduled_time, source_chat_id, source_message_id) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (media_file_id, media_type, caption, caption_entities, scheduled_time.isoformat(), source_chat_id, source_message_id),
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


def cancel_all_posts() -> int:
    """Cancel all pending posts. Returns the count of cancelled posts."""
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE scheduled_posts SET status = 'cancelled' WHERE status = 'pending'"
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count
