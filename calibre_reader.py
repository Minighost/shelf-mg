import os
import sqlite3
from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str
    authors: list[str]
    tags: list[str]  # generic — fandom, ship, rating, whatever you tag with
    summary_html: str  # Calibre's Comments field — stored as HTML, render accordingly
    series: str | None
    series_index: float | None
    epub_path: str  # absolute path to the actual .epub file on disk


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """
    Open metadata.db read-only. We never write to Calibre's library —
    calibre-web owns that job — so this also protects against ever
    accidentally corrupting it from this side.
    """
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def list_books(library_path: str) -> list[Book]:
    """
    Return every book in the Calibre library at library_path, with tags,
    summary, and the resolved on-disk path to its EPUB file.
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    books = []
    for row in conn.execute("SELECT id, title, path, series_index FROM books"):
        book_id = row["id"]

        authors = [
            r["name"]
            for r in conn.execute(
                """
                SELECT a.name FROM authors a
                JOIN books_authors_link bal ON bal.author = a.id
                WHERE bal.book = ?
                ORDER BY bal.id
                """,
                (book_id,),
            )
        ]

        tags = [
            r["name"]
            for r in conn.execute(
                """
                SELECT t.name FROM tags t
                JOIN books_tags_link btl ON btl.tag = t.id
                WHERE btl.book = ?
                ORDER BY t.name
                """,
                (book_id,),
            )
        ]

        comment_row = conn.execute(
            "SELECT text FROM comments WHERE book = ?", (book_id,)
        ).fetchone()
        summary_html = comment_row["text"] if comment_row else ""

        series_row = conn.execute(
            """
            SELECT s.name FROM series s
            JOIN books_series_link bsl ON bsl.series = s.id
            WHERE bsl.book = ?
            """,
            (book_id,),
        ).fetchone()
        series = series_row["name"] if series_row else None
        series_index = row["series_index"] if series else None

        # The actual EPUB filename isn't stored directly on `books` —
        # it's in `data`, one row per format the book has. We only care
        # about EPUB for this reader.
        data_row = conn.execute(
            "SELECT name FROM data WHERE book = ? AND format = 'EPUB'",
            (book_id,),
        ).fetchone()
        if data_row is None:
            continue  # book has no EPUB format — nothing for us to read

        epub_path = os.path.join(library_path, row["path"], data_row["name"] + ".epub")

        books.append(
            Book(
                id=book_id,
                title=row["title"],
                authors=authors,
                tags=tags,
                summary_html=summary_html,
                series=series,
                series_index=series_index,
                epub_path=epub_path,
            )
        )

    conn.close()
    return books
