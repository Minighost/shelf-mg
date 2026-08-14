import sqlite3
from dataclasses import dataclass


@dataclass
class Position:
    chapter_index: int
    updated_at: str


def init_db(db_path: str) -> None:
    """Create the positions table if it doesn't already exist. Safe to call every startup."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            book_id INTEGER PRIMARY KEY,
            chapter_index INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
        )
    """)
    conn.commit()
    conn.close()


def get_position(db_path: str, book_id: int) -> Position | None:
    """Return the saved position for a book, or None if it's never been read."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT chapter_index, updated_at FROM positions WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return Position(chapter_index=row["chapter_index"], updated_at=row["updated_at"])


def get_recent_positions(db_path: str, limit: int = 5) -> list[tuple[int, Position]]:
    """Return the `limit` most-recently-updated (book_id, Position) pairs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT book_id, chapter_index, updated_at FROM positions "
        "ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [
        (row["book_id"], Position(chapter_index=row["chapter_index"], updated_at=row["updated_at"]))
        for row in rows
    ]


def reset_all_positions(db_path: str) -> None:
    """Reset every book's saved chapter back to the first chapter, keeping the row
    (and its place in the "recently read" list) intact. Irreversible."""
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE positions SET chapter_index = 0")
    conn.commit()
    conn.close()


def clear_all_positions(db_path: str) -> None:
    """Delete every saved reading position outright, removing books from both
    "Continue reading" and the "recently read" list. Irreversible."""
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def save_position(db_path: str, book_id: int, chapter_index: int) -> Position:
    """
    Upsert the position for a book. One row per book — a new save always
    overwrites the old one, since there's only ever "where you last were."
    Returns the saved row (with the server-assigned updated_at) so callers
    can use it as a fresh conflict-check baseline.
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO positions (book_id, chapter_index, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
        ON CONFLICT(book_id) DO UPDATE SET
            chapter_index = excluded.chapter_index,
            updated_at = excluded.updated_at
    """,
        (book_id, chapter_index),
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT chapter_index, updated_at FROM positions WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    conn.close()
    return Position(chapter_index=row["chapter_index"], updated_at=row["updated_at"])
