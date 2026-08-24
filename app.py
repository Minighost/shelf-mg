import setup_logging

setup_logging.configure_logging()

import dotenv
import logging
import mimetypes
import os
import re
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import werkzeug.http
import flask
import flask_compress

import calibre_reader
import epub_parser
import positions
import search
import settings

logger = logging.getLogger(__name__)

dotenv.load_dotenv()

app = flask.Flask(__name__)
flask_compress.Compress(app)
# use gunicorn in prod:
# gunicorn -w 1 -b 0.0.0.0:5000 app:app

LIBRARY_PATH = os.environ.get("SHELF_MG_LIBRARY_PATH")
if not LIBRARY_PATH:
    raise RuntimeError(
        "SHELF_MG_LIBRARY_PATH environment variable is not set. "
        "Point it at your Calibre library folder (the one containing metadata.db)."
    )

DB_PATH = os.environ.get("SHELF_MG_DB_PATH", "shelf-mg.db")
positions.init_db(DB_PATH)
settings.init_db(DB_PATH)
search.init_db(DB_PATH)

BOOKS_PER_PAGE = 20
SEARCH_RESULTS_PER_PAGE = 20

logger.info(
    "startup: library=%s db=%s books=%d",
    LIBRARY_PATH,
    DB_PATH,
    len(calibre_reader.list_books(LIBRARY_PATH)),
)


@app.context_processor
def inject_settings():
    """
    Makes `settings` available in every template automatically, so each
    route doesn't need to remember to pass it explicitly — theme/font
    need to render correctly on every page, not just the ones a developer
    remembered to wire up.

    Additionally, makes the "last_synced" label available for all templates.
    """
    return {
        "settings": settings.get_settings(DB_PATH),
        "last_synced": _last_synced_label(),
    }


def _get_calibre_book(book_id):
    return calibre_reader.get_book(LIBRARY_PATH, book_id)


def _rewrite_image_srcs(
    html: str, book_id: int, chapter_index: int, chapter_dir: str
) -> str:
    pattern = re.compile(r'(<img\b[^>]*\bsrc=)(["\'])(.*?)\2', re.IGNORECASE)

    def replace(match):
        prefix, quote, src = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        resolved = epub_parser.resolve_relative_path(chapter_dir, src)
        new_src = f"/read/{book_id}/{chapter_index}/image/{resolved}"
        return f"{prefix}{quote}{new_src}{quote}"

    return pattern.sub(replace, html)


def _book_matches(book, query):
    query = query.lower()
    if query in book.title.lower():
        return True
    if any(query in author.lower() for author in book.authors):
        return True
    if any(query in tag.lower() for tag in book.tags):
        return True
    return False


def _builtin_filter_fields():
    """
    Filters tied to fixed Book fields. tags/author/series/publisher/title
    are always available (the app already depends on them unconditionally);
    date_added/pubdate/size/rating are conditionally included based on
    whether this specific library's Calibre schema actually has them —
    see calibre_reader.available_builtin_fields().
    """
    fields = [
        {"key": "title", "label": "Title", "type": "single", "datatype": "text"},
        {"key": "tags", "label": "Tags", "type": "multi", "datatype": "text"},
        {"key": "author", "label": "Author", "type": "single", "datatype": "text"},
        {"key": "series", "label": "Series", "type": "single", "datatype": "text"},
        {
            "key": "publisher",
            "label": "Publisher",
            "type": "single",
            "datatype": "text",
        },
    ]

    available = calibre_reader.available_builtin_fields(LIBRARY_PATH)
    if "date_added" in available:
        fields.append(
            {
                "key": "date_added",
                "label": "Date added",
                "type": "range",
                "datatype": "datetime",
            }
        )
    if "pubdate" in available:
        fields.append(
            {
                "key": "pubdate",
                "label": "Published",
                "type": "range",
                "datatype": "datetime",
            }
        )
    if "size" in available:
        fields.append(
            {"key": "size", "label": "Size (MB)", "type": "range", "datatype": "float"}
        )
    if "rating" in available:
        fields.append(
            {"key": "rating", "label": "Rating", "type": "range", "datatype": "float"}
        )

    return fields


BUILTIN_FILTER_GETTERS = {
    "title": lambda book: [book.title] if book.title else [],
    "tags": lambda book: book.tags,
    "author": lambda book: book.authors,
    "series": lambda book: [book.series] if book.series else [],
    "publisher": lambda book: [book.publisher] if book.publisher else [],
    "date_added": lambda book: [book.date_added] if book.date_added else [],
    "pubdate": lambda book: [book.pubdate] if book.pubdate else [],
    "size": lambda book: (
        [str(book.size_bytes / 1024 / 1024)] if book.size_bytes else []
    ),
    "rating": lambda book: [str(book.rating)] if book.rating is not None else [],
}

CUSTOM_FILTER_KEY_PREFIX = "custom:"


def _custom_filter_fields():
    """
    Filters discovered from the connected Calibre library's custom columns.
    Unlike BUILTIN_FILTER_FIELDS, this set varies per library, so it's
    recomputed from the live schema rather than hard-coded.
    """
    fields = []
    for column in calibre_reader.get_custom_columns(LIBRARY_PATH):
        if column["datatype"] in calibre_reader.RANGE_CUSTOM_DATATYPES:
            field_type = "range"
        elif column["is_multiple"]:
            field_type = "multi"
        else:
            field_type = "single"
        fields.append(
            {
                "key": CUSTOM_FILTER_KEY_PREFIX + column["label"],
                "label": column["name"],
                "type": field_type,
                "datatype": column["datatype"],
            }
        )
    return fields


def _all_filter_fields():
    return _builtin_filter_fields() + _custom_filter_fields()


def _filter_values(book, field):
    if field["key"] in BUILTIN_FILTER_GETTERS:
        return BUILTIN_FILTER_GETTERS[field["key"]](book)
    custom_label = field["key"][len(CUSTOM_FILTER_KEY_PREFIX) :]
    return book.custom.get(custom_label, [])


def _enabled_filter_fields():
    enabled = settings.get_settings(DB_PATH).enabled_filter_keys()
    fields_by_key = {field["key"]: field for field in _all_filter_fields()}
    return [fields_by_key[key] for key in enabled if key in fields_by_key]


def _list_view_fields():
    """
    Fields togglable in the library's list view: every filter/sort field
    except title (title always shows, it's needed to start reading), plus
    a synthetic "summary" field — summary isn't a filter field, but list
    view has always shown it, so it needs its own toggle.
    """
    fields = [f for f in _all_filter_fields() if f["key"] != "title"]
    fields.append(
        {"key": "summary", "label": "Summary", "type": "text", "datatype": None}
    )
    return fields


def _enabled_list_fields():
    enabled = settings.get_settings(DB_PATH).enabled_list_field_keys()
    fields_by_key = {field["key"]: field for field in _list_view_fields()}
    fields = [fields_by_key[key] for key in enabled if key in fields_by_key]
    # Summary carries its own trailing-block styling, so it always renders
    # last regardless of where it fell in the user's stored/dragged order.
    fields.sort(key=lambda field: field["key"] == "summary")
    return fields


def _filter_options(books, field):
    values = set()
    for book in books:
        values.update(_filter_values(book, field))
    return sorted(values)


def _typed_value(book, field):
    """
    A single, type-appropriate comparable value for this book+field, or
    None if the book has no value for it. Multi-value fields (e.g. tags)
    use the alphabetically-first value — same convention title-sort
    already used for tie-breaking. Shared by range-filtering and sorting
    so there's one place that understands how to compare each datatype.
    """
    raw_values = _filter_values(book, field)
    if not raw_values:
        return None

    datatype = field.get("datatype", "text")
    if datatype in ("int", "float"):
        try:
            return float(raw_values[0])
        except (TypeError, ValueError):
            return None
    if datatype == "datetime":
        try:
            return datetime.fromisoformat(raw_values[0])
        except (TypeError, ValueError):
            return None
    return min(v.lower() for v in raw_values)


def _range_bounds(books, field):
    """Min/max typed value across all books for a range-type field, or
    None if no book has a value for it — used to bound the filter UI."""
    values = [v for v in (_typed_value(b, field) for b in books) if v is not None]
    if not values:
        return None
    return (min(values), max(values))


def _book_matches_filters(book, filter_fields, selected):
    for field in filter_fields:
        if field["type"] == "range":
            bounds = selected.get(field["key"])
            if not bounds:
                continue
            min_v, max_v = bounds
            value = _typed_value(book, field)
            if value is None:
                return False
            if min_v is not None and value < min_v:
                return False
            if max_v is not None and value > max_v:
                return False
            continue

        chosen = selected.get(field["key"])
        if not chosen:
            continue
        book_values = set(_filter_values(book, field))
        if field["type"] == "multi":
            if not set(chosen).issubset(book_values):
                return False
        else:
            if chosen[0] not in book_values:
                return False
    return True


def _sort_books(books, sort_by, sort_dir, sort_fields):
    if sort_by == "title":
        return sorted(
            books, key=lambda b: b.title.lower(), reverse=(sort_dir == "desc")
        )

    field = next((f for f in sort_fields if f["key"] == sort_by), None)
    if field is None:
        return sorted(books, key=lambda b: b.title.lower())

    with_value = [b for b in books if _typed_value(b, field) is not None]
    without_value = [b for b in books if _typed_value(b, field) is None]
    with_value.sort(key=lambda b: _typed_value(b, field), reverse=(sort_dir == "desc"))
    return with_value + without_value


def _build_history_entries(rows):
    """Turn (book_id, Position) pairs into display dicts, resolving chapter
    titles and skipping books/chapters that no longer exist."""
    entries = []
    for book_id, pos in rows:
        book = _get_calibre_book(book_id)
        if book is None:
            continue
        try:
            epub_book = epub_parser.parse_book(book.epub_path)
            chapter_title = epub_book.chapters[pos.chapter_index].title
        except (IndexError, OSError):
            continue
        entries.append(
            {
                "book": book,
                "chapter_index": pos.chapter_index,
                "chapter_title": chapter_title,
                "updated_at": pos.updated_at,
            }
        )
    return entries


def _epub_error_description(exc: Exception) -> str:
    """Map the exception types epub_parser/zipfile can raise while reading
    a chapter into a distinct, user-facing explanation of what's wrong
    with the EPUB file."""
    if isinstance(exc, zipfile.BadZipFile):
        return "This book's EPUB file is corrupted or isn't a valid EPUB/zip archive."
    if isinstance(exc, ET.ParseError):
        return "This book's EPUB file contains malformed XML and can't be parsed."
    if isinstance(exc, KeyError):
        return "This book's EPUB file is missing a required internal file (e.g. its content or navigation file)."
    if isinstance(exc, OSError):
        return "This book's EPUB file couldn't be read from disk (it may have been moved or deleted)."
    return "This book's EPUB file couldn't be read."


def _last_synced_label() -> str:
    """Human-readable 'time since metadata.db was last modified', as a
    staleness indicator for the rsync-synced library."""
    db_path = os.path.join(LIBRARY_PATH, "metadata.db")
    mtime = datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)
    delta = datetime.now(timezone.utc) - mtime

    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


@app.route("/")
def library_list():
    query = flask.request.args.get("q", "").strip()
    page = flask.request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    current_settings = settings.get_settings(DB_PATH)
    view_param = flask.request.args.get("view")
    if view_param in settings.LIBRARY_VIEW_CHOICES:
        view = view_param
        if view != current_settings.library_view:
            settings.save_settings(
                DB_PATH,
                theme=current_settings.theme,
                font_family_key=current_settings.font_family_key,
                font_size=current_settings.font_size,
                library_view=view,
            )
    else:
        view = current_settings.library_view

    filter_fields = _enabled_filter_fields()
    sort_fields = _all_filter_fields()
    enabled_list_fields = _enabled_list_fields()
    selected = {}
    for field in filter_fields:
        if field["type"] == "range":
            min_raw = flask.request.args.get(field["key"] + "_min", "").strip()
            max_raw = flask.request.args.get(field["key"] + "_max", "").strip()
            is_datetime = field["datatype"] == "datetime"
            parse = datetime.fromisoformat if is_datetime else float
            try:
                min_v = parse(min_raw) if min_raw else None
            except ValueError:
                min_v = None
            try:
                max_v = parse(max_raw) if max_raw else None
            except ValueError:
                max_v = None
            if is_datetime:
                # The <input type="date"> posts a bare YYYY-MM-DD (timezone-
                # naive), but Calibre's stored datetimes carry a UTC offset
                # (confirmed via a real content.opf) — compare in UTC so
                # naive/aware comparisons in _book_matches_filters/_sort_books
                # don't raise.
                if min_v is not None and min_v.tzinfo is None:
                    min_v = min_v.replace(tzinfo=timezone.utc)
                if max_v is not None and max_v.tzinfo is None:
                    max_v = max_v.replace(tzinfo=timezone.utc)
            if min_v is not None or max_v is not None:
                selected[field["key"]] = (min_v, max_v)
        elif field["type"] == "multi":
            values = flask.request.args.getlist(field["key"])
            if values:
                selected[field["key"]] = values
        else:
            value = flask.request.args.get(field["key"], "").strip()
            if value:
                selected[field["key"]] = [value]

    sort_by = flask.request.args.get("sort", "title")
    sort_dir = flask.request.args.get("dir", "asc")
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    all_books = calibre_reader.list_books(LIBRARY_PATH)
    total_library = len(all_books)
    filter_options = {
        field["key"]: _filter_options(all_books, field)
        for field in filter_fields
        if field["type"] != "range"
    }
    range_bounds = {
        field["key"]: _range_bounds(all_books, field)
        for field in filter_fields
        if field["type"] == "range"
    }

    if query:
        all_books = [b for b in all_books if _book_matches(b, query)]
    all_books = [
        b for b in all_books if _book_matches_filters(b, filter_fields, selected)
    ]
    all_books = _sort_books(all_books, sort_by, sort_dir, sort_fields)

    total_pages = max(1, (len(all_books) + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * BOOKS_PER_PAGE
    books = all_books[start : start + BOOKS_PER_PAGE]

    book_positions = {
        book.id: positions.get_position(DB_PATH, book.id) for book in books
    }
    book_positions = {k: v for k, v in book_positions.items() if v is not None}

    continue_reading_entries = _build_history_entries(
        positions.get_recent_positions(DB_PATH, 1)
    )
    continue_reading = continue_reading_entries[0] if continue_reading_entries else None

    # Active filter/query/sort params, reusable for building pagination links
    # that preserve the current search+filter+sort+view state.
    page_args = {"q": query} if query else {}
    if sort_by != "title":
        page_args["sort"] = sort_by
    if sort_dir != "asc":
        page_args["dir"] = sort_dir
    for field in filter_fields:
        values = selected.get(field["key"])
        if not values:
            continue
        if field["type"] == "range":
            min_v, max_v = values
            if min_v is not None:
                page_args[field["key"] + "_min"] = (
                    min_v.date().isoformat()
                    if field["datatype"] == "datetime"
                    else min_v
                )
            if max_v is not None:
                page_args[field["key"] + "_max"] = (
                    max_v.date().isoformat()
                    if field["datatype"] == "datetime"
                    else max_v
                )
        else:
            page_args[field["key"]] = values

    # Same as page_args but with the view fixed to each option, for the
    # view-switch links.
    base_view_args = dict(page_args)
    view_links = {}
    for v in ("list", "grid", "card"):
        args = dict(base_view_args)
        args["view"] = v
        view_links[v] = args

    return flask.render_template(
        "library.html",
        books=books,
        positions=book_positions,
        continue_reading=continue_reading,
        page=page,
        total_pages=total_pages,
        query=query,
        filter_fields=filter_fields,
        sort_fields=sort_fields,
        filter_options=filter_options,
        range_bounds=range_bounds,
        selected=selected,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page_args=page_args,
        view=view,
        view_links=view_links,
        total_matches=len(all_books),
        total_library=total_library,
        enabled_list_fields=enabled_list_fields,
    )


@app.route("/history")
def history_page():
    page = flask.request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    total = positions.count_positions(DB_PATH)
    total_pages = max(1, (total + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    offset = (page - 1) * BOOKS_PER_PAGE
    rows = positions.get_positions_page(DB_PATH, BOOKS_PER_PAGE, offset)
    entries = _build_history_entries(rows)

    return flask.render_template(
        "history.html",
        entries=entries,
        page=page,
        total_pages=total_pages,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>")
def read_chapter(book_id, chapter_index):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        flask.abort(404, description="That book doesn't exist in your library.")

    try:
        epub_book = epub_parser.parse_book(calibre_book.epub_path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        logger.warning("failed to parse epub %s: %s", calibre_book.epub_path, e)
        flask.abort(404, description=_epub_error_description(e))

    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        flask.abort(404, description="That chapter doesn't exist.")

    chapter = epub_book.chapters[chapter_index]

    prev_index = chapter_index - 1 if chapter_index > 0 else None
    next_index = (
        chapter_index + 1 if chapter_index < len(epub_book.chapters) - 1 else None
    )

    saved = positions.get_position(DB_PATH, book_id)
    known_updated_at = saved.updated_at if saved is not None else None

    return flask.render_template(
        "reader.html",
        book=calibre_book,
        chapter={"index": chapter.index, "title": chapter.title},
        chapters=epub_book.chapters,
        prev_index=prev_index,
        next_index=next_index,
        known_updated_at=known_updated_at,
    )


@app.route("/inspect/<int:book_id>")
def inspect_book(book_id):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        flask.abort(404, description="That book doesn't exist in your library.")

    details = calibre_reader.get_book_details(LIBRARY_PATH, book_id)
    if details is None:
        flask.abort(404, description="That book doesn't exist in your library.")

    # The epub itself might be corrupt/missing even though Calibre has a
    # record of it — this page should still render the Calibre-side info
    # rather than 500ing, so chapter/technical data degrades gracefully.
    try:
        epub_book = epub_parser.parse_book(calibre_book.epub_path)
        stats = epub_parser.get_epub_stats(calibre_book.epub_path, epub_book)
        parse_error = None
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        logger.warning("failed to parse epub %s: %s", calibre_book.epub_path, e)
        epub_book, stats, parse_error = None, None, str(e)

    has_cover = calibre_reader.find_cover_image(calibre_book.epub_path) is not None

    position = positions.get_position(DB_PATH, book_id)
    progress = None
    if position is not None and epub_book is not None and epub_book.chapters:
        total = len(epub_book.chapters)
        current_title = (
            epub_book.chapters[position.chapter_index].title
            if 0 <= position.chapter_index < total
            else None
        )
        progress = {
            "chapter_index": position.chapter_index,
            "chapter_title": current_title,
            "percent": round((position.chapter_index / total) * 100),
            "updated_at": position.updated_at,
        }

    file_size = None
    if stats is not None:
        file_size = f"{stats.file_size_bytes / 1024 / 1024:.1f} MB"

    return flask.render_template(
        "inspect.html",
        book=calibre_book,
        details=details,
        chapters=(epub_book.chapters if epub_book else []),
        stats=stats,
        file_size=file_size,
        parse_error=parse_error,
        has_cover=has_cover,
        progress=progress,
    )


@app.route("/inspect/<int:book_id>/cover")
def inspect_cover(book_id):
    epub_path = calibre_reader.get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        flask.abort(404, description="That book doesn't exist in your library.")

    cover_path = calibre_reader.find_cover_image(epub_path)
    if cover_path is None:
        flask.abort(404, description="No cover image for that book.")

    thumbnail_bytes = calibre_reader.get_cover_thumbnail(cover_path)

    return flask.Response(
        thumbnail_bytes,
        mimetype="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Last-Modified": werkzeug.http.http_date(os.path.getmtime(cover_path)),
        },
    )


@app.route("/read/<int:book_id>/<int:chapter_index>/frame")
def read_chapter_frame(book_id, chapter_index):
    epub_path = calibre_reader.get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        flask.abort(404, description="That book doesn't exist in your library.")

    try:
        epub_book = epub_parser.parse_book(epub_path)
        if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
            flask.abort(404, description="That chapter doesn't exist.")

        content = epub_parser.get_chapter_content(epub_path, epub_book, chapter_index)
        chapter_dir = epub_parser.get_chapter_dir(
            epub_book, epub_book.chapters[chapter_index]
        )
        content = _rewrite_image_srcs(content, book_id, chapter_index, chapter_dir)
        css = epub_parser.get_stylesheets(epub_path, epub_book)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        logger.warning("failed to parse epub %s: %s", epub_path, e)
        flask.abort(404, description=_epub_error_description(e))

    return flask.render_template(
        "chapter_frame.html",
        css=css,
        content=content,
        chapter_title=epub_book.chapters[chapter_index].title,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>/image/<path:img_path>")
def read_chapter_image(book_id, chapter_index, img_path):
    epub_path = calibre_reader.get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        flask.abort(404)

    try:
        with zipfile.ZipFile(epub_path) as zf:
            data = zf.read(img_path)
    except KeyError:
        flask.abort(404)

    mime_type, _ = mimetypes.guess_type(img_path)
    return flask.Response(data, mimetype=mime_type or "application/octet-stream")


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if flask.request.method == "POST":
        theme = flask.request.form.get("theme", "").strip()
        font_family_key = flask.request.form.get("font_family", "").strip()
        font_size_raw = flask.request.form.get("font_size", "")
        override_epub_font = flask.request.form.get("override_epub_font") is not None
        reader_font_family_key = flask.request.form.get(
            "reader_font_family", ""
        ).strip()
        reader_font_size_raw = flask.request.form.get("reader_font_size", "")
        content_max_width_pct_raw = flask.request.form.get("content_max_width_pct", "")
        enabled_filters = flask.request.form.getlist("enabled_filters")
        enabled_list_fields = flask.request.form.getlist("enabled_list_fields")

        try:
            font_size = float(font_size_raw)
            reader_font_size = (
                float(reader_font_size_raw) if reader_font_size_raw else None
            )
            content_max_width_pct = (
                float(content_max_width_pct_raw) if content_max_width_pct_raw else None
            )
        except ValueError:
            return "Invalid font size", 400

        custom_colors = {
            field: flask.request.form.get(field, "").strip()
            for field in settings.CUSTOM_COLOR_FIELDS
        }

        valid_filter_keys = {field["key"] for field in _all_filter_fields()}
        invalid_filter_keys = set(enabled_filters) - valid_filter_keys
        if invalid_filter_keys:
            return (
                f"Invalid filter key(s): {', '.join(sorted(invalid_filter_keys))}",
                400,
            )

        valid_list_field_keys = {field["key"] for field in _list_view_fields()}
        invalid_list_field_keys = set(enabled_list_fields) - valid_list_field_keys
        if invalid_list_field_keys:
            return (
                f"Invalid list field key(s): {', '.join(sorted(invalid_list_field_keys))}",
                400,
            )

        try:
            settings.save_settings(
                DB_PATH,
                theme=theme,
                font_family_key=font_family_key,
                font_size=font_size,
                override_epub_font=override_epub_font,
                reader_font_family_key=reader_font_family_key or None,
                reader_font_size=reader_font_size,
                content_max_width_pct=content_max_width_pct,
                custom_colors=custom_colors,
                enabled_filters=enabled_filters,
                enabled_list_fields=enabled_list_fields,
            )
        except ValueError as e:
            return str(e), 400

        back = flask.request.form.get("back", "")
        if back:
            return flask.redirect(f"{flask.url_for('library_list')}?{back}")
        return flask.redirect(flask.url_for("library_list"))

    return flask.render_template(
        "settings.html",
        theme_choices=settings.THEME_CHOICES,
        font_choices=settings.FONT_CHOICES,
        filter_fields=_all_filter_fields(),
        active_filter_fields=_enabled_filter_fields(),
        list_fields=_list_view_fields(),
        active_list_fields=_enabled_list_fields(),
    )


@app.route("/settings/theme", methods=["POST"])
def update_theme():
    data = flask.request.get_json(silent=True) or {}
    theme = data.get("theme", "").strip()
    if theme not in ("light", "dark"):  # custom isn't reachable from the quick toggle
        return flask.jsonify({"error": "invalid theme"}), 400

    current = settings.get_settings(DB_PATH)
    settings.save_settings(
        DB_PATH,
        theme=theme,
        font_family_key=current.font_family_key,
        font_size=current.font_size,
    )
    return flask.jsonify({"status": "ok"})


@app.route("/settings/reset-progress", methods=["POST"])
def reset_progress():
    positions.reset_all_positions(DB_PATH)
    back = flask.request.form.get("back", "")
    if back:
        return flask.redirect(
            f"{flask.url_for('settings_page')}?back={urllib.parse.quote(back, safe='')}"
        )
    return flask.redirect(flask.url_for("settings_page"))


@app.route("/settings/clear-recent", methods=["POST"])
def clear_recent():
    positions.clear_all_positions(DB_PATH)
    back = flask.request.form.get("back", "")
    if back:
        return flask.redirect(
            f"{flask.url_for('settings_page')}?back={urllib.parse.quote(back, safe='')}"
        )
    return flask.redirect(flask.url_for("settings_page"))


@app.route("/api/position/<int:book_id>", methods=["GET", "POST"])
def position_api(book_id):
    if flask.request.method == "GET":
        saved = positions.get_position(DB_PATH, book_id)
        if saved is None:
            return flask.jsonify(None)
        return flask.jsonify(
            {"chapter_index": saved.chapter_index, "updated_at": saved.updated_at}
        )

    data = flask.request.get_json(silent=True) or {}
    chapter_index = data.get("chapter_index")

    if chapter_index is None:
        return flask.jsonify({"error": "chapter_index is required"}), 400

    saved = positions.save_position(DB_PATH, book_id, int(chapter_index))
    logger.debug("saved position for book %s: chapter %s", book_id, chapter_index)
    return flask.jsonify(
        {"chapter_index": saved.chapter_index, "updated_at": saved.updated_at}
    )


@app.route("/settings/refresh-library", methods=["POST"])
def refresh_library():
    logger.info("manual cache refresh triggered")
    calibre_reader.clear_cache()
    epub_parser.clear_cache()
    qs = flask.request.form.get("qs", "")
    if qs:
        return flask.redirect(f"{flask.url_for('library_list')}?{qs}")
    return flask.redirect(flask.url_for("library_list"))


@app.route("/settings/reindex-search", methods=["POST"])
def reindex_search():
    started = search.start_reindex(LIBRARY_PATH, DB_PATH)
    if not started:
        return flask.jsonify({"status": "already_running"}), 409
    return flask.jsonify({"status": "started"})


@app.route("/settings/reindex-search/status")
def reindex_status():
    return flask.jsonify(search.get_status())


@app.route("/search")
def search_results():
    query = flask.request.args.get("q", "").strip()
    page = max(flask.request.args.get("page", 1, type=int), 1)

    results = []
    total_matches = 0
    total_pages = 1

    if query:
        total_matches = search.count_chapters(DB_PATH, query)
        total_pages = max(1, -(-total_matches // SEARCH_RESULTS_PER_PAGE))
        page = min(page, total_pages)

        offset = (page - 1) * SEARCH_RESULTS_PER_PAGE
        rows = search.query_chapters(DB_PATH, query, SEARCH_RESULTS_PER_PAGE, offset)
        for row in rows:
            book = calibre_reader.get_book(LIBRARY_PATH, row["book_id"])
            if book is None:
                continue
            results.append(
                {
                    "book_id": book.id,
                    "book_title": book.title,
                    "chapter_index": row["chapter_index"],
                    "chapter_title": row["chapter_title"],
                    "snippet": search.render_snippet(row["snippet"]),
                }
            )

    return flask.render_template(
        "search_results.html",
        query=query,
        results=results,
        page=page,
        total_pages=total_pages,
        total_matches=total_matches,
    )


@app.errorhandler(404)
def not_found(error):
    message = getattr(error, "description", None)
    return flask.render_template("404.html", message=message), 404


@app.errorhandler(500)
def server_error(error):
    logger.exception("unhandled server error")
    return flask.render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
