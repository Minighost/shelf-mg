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


def _custom_column_values(
    conn: sqlite3.Connection, book_id: int, column: dict
) -> list[str]:
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


def _build_book(
    conn: sqlite3.Connection,
    library_path: str,
    custom_columns: list[dict],
    book_id: int,
    title: str,
    path: str,
    raw_series_index: float | None,
) -> Book | None:
    """
    Assemble one Book from its `books` row plus the joins that don't live on
    that row directly. Returns None if the book has no EPUB format on disk
    (nothing for this reader to open).
    """
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
    series_index = raw_series_index if series else None

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
        return None  # book has no EPUB format — nothing for us to read

    epub_path = os.path.join(library_path, path, data_row["name"] + ".epub")

    return Book(
        id=book_id,
        title=title,
        authors=authors,
        tags=tags,
        summary_html=summary_html,
        series=series,
        series_index=series_index,
        publisher=publisher,
        custom=custom,
        epub_path=epub_path,
    )


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
        book = _build_book(
            conn,
            library_path,
            custom_columns,
            row["id"],
            row["title"],
            row["path"],
            row["series_index"],
        )
        if book is not None:
            books.append(book)

    conn.close()
    return books


def get_book(library_path: str, book_id: int) -> Book | None:
    """
    Look up a single book by id without scanning the whole library — used by
    routes that only need one book (cover images, chapter reads, history
    entries) so their cost doesn't grow with library size.
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT id, title, path, series_index FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return None

    custom_columns = get_custom_columns(library_path)
    book = _build_book(
        conn,
        library_path,
        custom_columns,
        row["id"],
        row["title"],
        row["path"],
        row["series_index"],
    )

    conn.close()
    return book


def get_book_epub_path(library_path: str, book_id: int) -> str | None:
    """
    Resolve just a book's on-disk EPUB path, skipping the full author/tag/
    series/custom-column assembly _build_book does — for routes (cover
    images, chapter frames, chapter images) that only ever need epub_path.
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        conn.close()
        return None

    data_row = conn.execute(
        "SELECT name FROM data WHERE book = ? AND format = 'EPUB'", (book_id,)
    ).fetchone()
    conn.close()
    if data_row is None:
        return None

    return os.path.join(library_path, row["path"], data_row["name"] + ".epub")


@dataclass
class BookDetails:
    rating: float | None  # 0-5 stars
    date_added: str | None  # books.timestamp
    pubdate: str | None  # books.pubdate
    identifiers: dict[
        str, str
    ]  # free-form Calibre keys, e.g. isbn/amazon/url/goodreads


def get_book_details(library_path: str, book_id: int) -> BookDetails | None:
    """
    Extra Calibre metadata not needed by the main library list (rating, dates,
    identifiers) — queried separately, on demand, so the per-book cost here
    doesn't get paid on every `/` request by list_books().
    """
    db_path = os.path.join(library_path, "metadata.db")
    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    book_row = conn.execute(
        "SELECT timestamp, pubdate FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if book_row is None:
        conn.close()
        return None

    # Calibre stores rating as 0-10 (2x the star count) to allow half-stars.
    rating_row = conn.execute(
        """
        SELECT r.rating FROM ratings r
        JOIN books_ratings_link brl ON brl.rating = r.id
        WHERE brl.book = ?
        """,
        (book_id,),
    ).fetchone()
    rating = rating_row["rating"] / 2 if rating_row else None

    identifiers = {
        r["type"]: r["val"]
        for r in conn.execute(
            "SELECT type, val FROM identifiers WHERE book = ?", (book_id,)
        )
    }

    conn.close()
    return BookDetails(
        rating=rating,
        date_added=book_row["timestamp"],
        pubdate=book_row["pubdate"],
        identifiers=identifiers,
    )
