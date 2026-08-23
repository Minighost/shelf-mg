import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

# Custom-column datatypes with a bounded, listable set of values — filterable
# as a checklist/dropdown.
DISCRETE_CUSTOM_DATATYPES = {"text", "enumeration", "series", "rating", "bool"}
# Continuous-valued datatypes — filterable/sortable as a min/max range instead.
RANGE_CUSTOM_DATATYPES = {"int", "float", "datetime"}
# Skips comments (long-form) and composite (computed from other columns).
FILTERABLE_CUSTOM_DATATYPES = DISCRETE_CUSTOM_DATATYPES | RANGE_CUSTOM_DATATYPES

COVER_THUMBNAIL_MAX_SIZE = (160, 240)  # matches the inspect page's 160px display width


@dataclass
class Book:
    id: int
    title: str
    authors: list[str]
    # generic — fandom, ship, rating, whatever you tag with
    tags: list[str]
    # Calibre's Comments field — stored as HTML, render accordingly
    summary_html: str
    series: str | None
    series_index: float | None
    publisher: str | None
    # custom-column label -> values, always list-valued
    custom: dict[str, list[str]]
    # absolute path to the actual .epub file on disk
    epub_path: str
    # books.timestamp, ISO 8601 text — None if that column is missing from this library's schema
    date_added: str | None
    # books.pubdate, ISO 8601 text — same caveat
    pubdate: str | None
    # data.uncompressed_size for the EPUB format row — same caveat
    size_bytes: int | None
    # 0-5 stars (Calibre stores 0-10 internally; halved here, same convention as get_book_details()) — same caveat
    rating: float | None


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    """
    Open metadata.db read-only. We never write to Calibre's library —
    calibre-web owns that job — so this also protects against ever
    accidentally corrupting it from this side.
    """
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


# Module-level cache: library_path -> (mtime_at_cache_time, columns_list).
# Separate from _list_books_cache since callers like _all_filter_fields()
# only need columns, not the full book list — no reason to force a full
# library scan just to answer "what custom columns exist."
_custom_columns_cache: dict[str, tuple[float, list[dict]]] = {}


def get_custom_columns(library_path: str) -> list[dict]:
    """
    Discover this library's user-defined custom columns, keeping only the
    datatypes with a bounded, listable set of values (see
    FILTERABLE_CUSTOM_DATATYPES) — the ones that make sense as a filter.

    Cached per library_path, invalidated automatically whenever
    metadata.db's mtime changes.
    """
    db_path = os.path.join(library_path, "metadata.db")
    current_mtime = os.path.getmtime(db_path)

    cached = _custom_columns_cache.get(library_path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

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

    _custom_columns_cache[library_path] = (current_mtime, columns)
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


def _bulk_custom_column_values(
    conn: sqlite3.Connection, column: dict
) -> dict[int, list[str]]:
    """
    Same normalized/non-normalized branching as _custom_column_values, but
    across every book in one query instead of one query per (book, column)
    pair — used by list_books()'s bulk path.
    """
    table = f"custom_column_{column['col_id']}"
    values_by_book: dict[int, list[str]] = defaultdict(list)

    if column["normalized"]:
        link_table = f"books_custom_column_{column['col_id']}_link"
        for r in conn.execute(
            f"""
            SELECT l.book AS book_id, v.value AS value FROM {table} v
            JOIN {link_table} l ON l.value = v.id
            ORDER BY l.book, l.value
            """
        ):
            values_by_book[r["book_id"]].append(str(r["value"]))
        return values_by_book

    # Non-normalized (bool): one row of (book, value) per book directly.
    for r in conn.execute(f"SELECT book, value FROM {table}"):
        if r["value"] is not None:
            values_by_book[r["book"]] = ["Yes" if r["value"] else "No"]
    return values_by_book


_BUILTIN_FIELD_REQUIREMENTS = {
    "date_added": ("books", "timestamp"),
    "pubdate": ("books", "pubdate"),
    "size": ("data", "uncompressed_size"),
    "rating": ("ratings", "rating"),
}

# Module-level cache: library_path -> (mtime_at_cache_time, available_fields_set).
_available_builtin_fields_cache: dict[str, tuple[float, set[str]]] = {}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Actual column names present in this specific metadata.db's version
    of a table — used to defensively skip built-in fields that don't exist
    in an older/newer Calibre schema, instead of crashing on a missing
    column."""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def available_builtin_fields(library_path: str) -> set[str]:
    """
    Which of shelf-mg's optional built-in fields (date_added, pubdate,
    size, rating) actually exist in this library's schema — guards against
    older/newer Calibre versions that may be missing a column shelf-mg
    otherwise assumes is there. tags/author/series/publisher aren't part
    of this check since the app already depends on them unconditionally.

    Cached per library_path, invalidated automatically whenever
    metadata.db's mtime changes.
    """
    db_path = os.path.join(library_path, "metadata.db")
    current_mtime = os.path.getmtime(db_path)

    cached = _available_builtin_fields_cache.get(library_path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    available = set()
    for field_key, (table, column) in _BUILTIN_FIELD_REQUIREMENTS.items():
        if column in _table_columns(conn, table):
            available.add(field_key)

    conn.close()

    _available_builtin_fields_cache[library_path] = (current_mtime, available)
    return available


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
        "SELECT name, uncompressed_size FROM data WHERE book = ? AND format = 'EPUB'",
        (book_id,),
    ).fetchone()
    if data_row is None:
        return None  # book has no EPUB format — nothing for us to read

    epub_path = os.path.join(library_path, path, data_row["name"] + ".epub")
    size_bytes = data_row["uncompressed_size"]

    available_fields = available_builtin_fields(library_path)

    date_added = None
    pubdate = None
    if "date_added" in available_fields or "pubdate" in available_fields:
        book_row = conn.execute(
            "SELECT timestamp, pubdate FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        if "date_added" in available_fields:
            date_added = book_row["timestamp"]
        if "pubdate" in available_fields:
            pubdate = book_row["pubdate"]

    rating = None
    if "rating" in available_fields:
        rating_row = conn.execute(
            """
            SELECT r.rating FROM ratings r
            JOIN books_ratings_link brl ON brl.rating = r.id
            WHERE brl.book = ?
            """,
            (book_id,),
        ).fetchone()
        rating = rating_row["rating"] / 2 if rating_row else None

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
        date_added=date_added,
        pubdate=pubdate,
        size_bytes=size_bytes,
        rating=rating,
    )


def _build_books_bulk(
    conn: sqlite3.Connection,
    library_path: str,
    custom_columns: list[dict],
    book_rows: list[sqlite3.Row],
    available_fields: set[str],
) -> list[Book]:
    """
    Bulk-query equivalent of calling _build_book() once per row in
    book_rows — same per-book semantics (including skipping books with no
    EPUB format), but one query per relation across the whole library
    instead of one query per relation per book. Used only by list_books();
    get_book() keeps using _build_book() directly since it's already a
    single-row O(1) lookup.
    """
    authors_by_book: dict[int, list[str]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT bal.book AS book_id, a.name AS name FROM authors a
        JOIN books_authors_link bal ON bal.author = a.id
        ORDER BY bal.book, bal.id
        """
    ):
        authors_by_book[r["book_id"]].append(r["name"])

    tags_by_book: dict[int, list[str]] = defaultdict(list)
    for r in conn.execute(
        """
        SELECT btl.book AS book_id, t.name AS name FROM tags t
        JOIN books_tags_link btl ON btl.tag = t.id
        ORDER BY btl.book, t.name
        """
    ):
        tags_by_book[r["book_id"]].append(r["name"])

    comment_by_book = {
        r["book"]: r["text"] for r in conn.execute("SELECT book, text FROM comments")
    }

    series_by_book = {
        r["book_id"]: r["name"]
        for r in conn.execute(
            """
            SELECT bsl.book AS book_id, s.name AS name FROM series s
            JOIN books_series_link bsl ON bsl.series = s.id
            """
        )
    }

    publisher_by_book = {
        r["book_id"]: r["name"]
        for r in conn.execute(
            """
            SELECT bpl.book AS book_id, p.name AS name FROM publishers p
            JOIN books_publishers_link bpl ON bpl.publisher = p.id
            """
        )
    }

    epub_by_book = {
        r["book"]: (r["name"], r["uncompressed_size"])
        for r in conn.execute(
            "SELECT book, name, uncompressed_size FROM data WHERE format = 'EPUB'"
        )
    }

    rating_by_book: dict[int, float] = {}
    if "rating" in available_fields:
        rating_by_book = {
            r["book_id"]: r["rating"] / 2
            for r in conn.execute(
                """
                SELECT brl.book AS book_id, r.rating AS rating FROM ratings r
                JOIN books_ratings_link brl ON brl.rating = r.id
                """
            )
        }

    custom_values_by_column = {
        column["label"]: _bulk_custom_column_values(conn, column)
        for column in custom_columns
    }

    books = []
    for row in book_rows:
        book_id = row["id"]

        epub_data = epub_by_book.get(book_id)
        if epub_data is None:
            continue  # book has no EPUB format — nothing for us to read
        epub_name, size_bytes = epub_data

        series = series_by_book.get(book_id)

        books.append(
            Book(
                id=book_id,
                title=row["title"],
                authors=authors_by_book.get(book_id, []),
                tags=tags_by_book.get(book_id, []),
                summary_html=comment_by_book.get(book_id, ""),
                series=series,
                series_index=row["series_index"] if series else None,
                publisher=publisher_by_book.get(book_id),
                custom={
                    column["label"]: custom_values_by_column[column["label"]].get(
                        book_id, []
                    )
                    for column in custom_columns
                },
                epub_path=os.path.join(library_path, row["path"], epub_name + ".epub"),
                date_added=(
                    row["date_added"] if "date_added" in available_fields else None
                ),
                pubdate=row["pubdate"] if "pubdate" in available_fields else None,
                size_bytes=size_bytes,
                rating=rating_by_book.get(book_id),
            )
        )

    return books


# Module-level cache: library_path -> (mtime_at_cache_time, books_list).
# Invalidated by comparing metadata.db's current mtime against what's
# stored — if the file hasn't changed since we last scanned it, the
# scan result is still valid, so skip re-querying Calibre entirely.
_list_books_cache: dict[str, tuple[float, list[Book]]] = {}


def list_books(library_path: str) -> list[Book]:
    """
    Return every book in the Calibre library at library_path, with tags,
    summary, and the resolved on-disk path to its EPUB file.

    Cached per library_path, invalidated automatically whenever
    metadata.db's mtime changes (i.e. whenever Calibre itself — or
    anything else — modifies the library). Safe to call this on every
    request; repeated calls with no underlying change are O(1) after
    the first.
    """
    db_path = os.path.join(library_path, "metadata.db")
    current_mtime = os.path.getmtime(db_path)

    cached = _list_books_cache.get(library_path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    conn = _connect_readonly(db_path)
    conn.row_factory = sqlite3.Row

    custom_columns = get_custom_columns(library_path)
    available_fields = available_builtin_fields(library_path)

    select_cols = ["id", "title", "path", "series_index"]
    if "date_added" in available_fields:
        select_cols.append("timestamp AS date_added")
    if "pubdate" in available_fields:
        select_cols.append("pubdate")
    book_rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM books").fetchall()

    books = _build_books_bulk(
        conn, library_path, custom_columns, book_rows, available_fields
    )

    conn.close()

    _list_books_cache[library_path] = (current_mtime, books)
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


def find_cover_image(epub_path: str) -> str | None:
    """
    Calibre stores a book's cover as cover.jpg beside the EPUB file in the
    book's own library folder (not inside the EPUB zip itself) — this is a
    Calibre library-layout convention, not an EPUB-format one. Moved here
    from epub_parser.py for that reason.
    """
    cover_path = os.path.join(os.path.dirname(epub_path), "cover.jpg")
    return cover_path if os.path.isfile(cover_path) else None


# Module-level cache: cover_path -> (mtime_at_cache_time, resized_jpeg_bytes).
_cover_thumbnail_cache: dict[str, tuple[float, bytes]] = {}


def get_cover_thumbnail(cover_path: str) -> bytes:
    """
    Return a resized (COVER_THUMBNAIL_MAX_SIZE), JPEG-encoded thumbnail of
    a cover image, generated on first request and cached thereafter.

    Cached per cover_path, invalidated automatically whenever that specific
    file's mtime changes (e.g. a replaced cover.jpg). In-memory only —
    doesn't persist across app restarts.
    """
    current_mtime = os.path.getmtime(cover_path)

    cached = _cover_thumbnail_cache.get(cover_path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    with Image.open(cover_path) as img:
        # Hint the JPEG decoder to decode at a reduced resolution directly,
        # instead of fully decoding a multi-megapixel source image just to
        # immediately throw most of that resolution away. Cheap no-op for
        # already-small sources; real speedup for large Amazon/Goodreads-
        # sourced covers, which can be several MB / 2000px+ wide.
        img.draft("RGB", COVER_THUMBNAIL_MAX_SIZE)
        img = img.convert("RGB")
        img.thumbnail(COVER_THUMBNAIL_MAX_SIZE)
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        thumbnail_bytes = buffer.getvalue()

    _cover_thumbnail_cache[cover_path] = (current_mtime, thumbnail_bytes)
    return thumbnail_bytes


def clear_cache() -> None:
    """
    Manually drop every cached Calibre-derived value (book list, custom
    columns, cover thumbnails) regardless of mtime. Normally unnecessary —
    the mtime checks already catch real changes automatically — but useful
    as an explicit "I just edited something in Calibre, refresh now" escape
    hatch, and as a safety net in case a change doesn't bump mtime the way
    it's expected to.
    """
    _list_books_cache.clear()
    _custom_columns_cache.clear()
    _cover_thumbnail_cache.clear()
