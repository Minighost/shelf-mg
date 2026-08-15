import os
import sqlite3
from dataclasses import dataclass


# Custom-column datatypes with a bounded, listable set of values — the only
# ones that make sense as a filter dropdown/checklist. Skips comments
# (long-form), int/float/datetime (continuous), and composite (computed).
FILTERABLE_CUSTOM_DATATYPES = {"text", "enumeration", "series", "rating", "bool"}


@dataclass
class Book:
    id: int
    title: str
    authors: list[str]
    tags: list[str]  # generic — fandom, ship, rating, whatever you tag with
    summary_html: str  # Calibre's Comments field — stored as HTML, render accordingly
    series: str | None
    series_index: float | None
    publisher: str | None
    custom: dict[str, list[str]]  # custom-column label -> values, always list-valued
    epub_path: str  # absolute path to the actual .epub file on disk


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """
    Open metadata.db read-only. We never write to Calibre's library —
    calibre-web owns that job — so this also protects against ever
    accidentally corrupting it from this side.
    """
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_custom_columns(library_path: str) -> list[dict]:
    """
    Discover this library's user-defined custom columns, keeping only the
    datatypes with a bounded, listable set of values (see
    FILTERABLE_CUSTOM_DATATYPES) — the ones that make sense as a filter.
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    columns = [
        {
            "col_id": row["id"],
            # Calibre's `label` is the internal key, `name` is the display name.
            "label": row["label"],
            "name": row["name"],
            "datatype": row["datatype"],
            "is_multiple": bool(row["is_multiple"]),
            "normalized": bool(row["normalized"]),
        }
        for row in conn.execute(
            "SELECT id, label, name, datatype, is_multiple, normalized FROM custom_columns"
        )
        if row["datatype"] in FILTERABLE_CUSTOM_DATATYPES
    ]

    conn.close()
    return columns


def _custom_column_values(conn: sqlite3.Connection, book_id: int, column: dict) -> list[str]:
    table = f"custom_column_{column['col_id']}"
    if column["normalized"]:
        link_table = f"books_custom_column_{column['col_id']}_link"
        return [
            str(r["value"])
            for r in conn.execute(
                f"""
                SELECT v.value AS value FROM {table} v
                JOIN {link_table} l ON l.value = v.id
                WHERE l.book = ?
                """,
                (book_id,),
            )
        ]

    # Non-normalized (bool): one row of (id, book, value) directly.
    row = conn.execute(
        f"SELECT value FROM {table} WHERE book = ?", (book_id,)
    ).fetchone()
    if row is None or row["value"] is None:
        return []
    return ["Yes" if row["value"] else "No"]


def list_books(library_path: str) -> list[Book]:
    """
    Return every book in the Calibre library at library_path, with tags,
    summary, and the resolved on-disk path to its EPUB file.
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    custom_columns = get_custom_columns(library_path)

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

        publisher_row = conn.execute(
            """
            SELECT p.name FROM publishers p
            JOIN books_publishers_link bpl ON bpl.publisher = p.id
            WHERE bpl.book = ?
            """,
            (book_id,),
        ).fetchone()
        publisher = publisher_row["name"] if publisher_row else None

        custom = {
            column["label"]: _custom_column_values(conn, book_id, column)
            for column in custom_columns
        }

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
                publisher=publisher,
                custom=custom,
                epub_path=epub_path,
            )
        )

    conn.close()
    return books
