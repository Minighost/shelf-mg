import sqlite3
from dataclasses import dataclass


@dataclass
class Position:
    chapter_index: int
    scroll_percent: float  # 0.0 to 1.0 — how far down the chapter you were


def init_db(db_path: str) -> None:
    """Create the positions table if it doesn't already exist. Safe to call every startup."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            book_id INTEGER PRIMARY KEY,
            chapter_index INTEGER NOT NULL,
            scroll_percent REAL NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def get_position(db_path: str, book_id: int) -> Position | None:
    """Return the saved position for a book, or None if it's never been read."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT chapter_index, scroll_percent FROM positions WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return Position(
        chapter_index=row["chapter_index"], scroll_percent=row["scroll_percent"]
    )


def save_position(
    db_path: str, book_id: int, chapter_index: int, scroll_percent: float
) -> None:
    """
    Upsert the position for a book. One row per book — a new save always
    overwrites the old one, since there's only ever "where you last were."
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO positions (book_id, chapter_index, scroll_percent, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(book_id) DO UPDATE SET
            chapter_index = excluded.chapter_index,
            scroll_percent = excluded.scroll_percent,
            updated_at = excluded.updated_at
    """,
        (book_id, chapter_index, scroll_percent),
    )
    conn.commit()
    conn.close()
