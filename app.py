import mimetypes
import os
import re
import zipfile
from xml.etree import ElementTree as ET

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    abort,
    redirect,
    send_file,
    url_for,
)

from calibre_reader import (
    list_books,
    get_book,
    get_book_epub_path,
    get_custom_columns,
    get_book_details,
)
from epub_parser import (
    parse_book,
    get_chapter_content,
    get_stylesheets,
    get_chapter_dir,
    resolve_relative_path,
    find_cover_image,
    get_epub_stats,
)
from positions import (
    init_db as init_positions_db,
    clear_all_positions,
    count_positions,
    get_position,
    get_positions_page,
    get_recent_positions,
    reset_all_positions,
    save_position,
)
from settings import (
    init_db as init_settings_db,
    get_settings,
    save_settings,
    FONT_CHOICES,
    THEME_CHOICES,
    CUSTOM_COLOR_FIELDS,
)

app = Flask(__name__)

LIBRARY_PATH = os.environ.get("SHELF_MG_LIBRARY_PATH")
if not LIBRARY_PATH:
    raise RuntimeError(
        "SHELF_MG_LIBRARY_PATH environment variable is not set. "
        "Point it at your Calibre library folder (the one containing metadata.db)."
    )

DB_PATH = os.environ.get("SHELF_MG_DB_PATH", "shelf-mg.db")
init_positions_db(DB_PATH)
init_settings_db(DB_PATH)

BOOKS_PER_PAGE = 20


@app.context_processor
def inject_settings():
    """
    Makes `settings` available in every template automatically, so each
    route doesn't need to remember to pass it explicitly — theme/font
    need to render correctly on every page, not just the ones a developer
    remembered to wire up.
    """
    return {"settings": get_settings(DB_PATH)}


def _get_calibre_book(book_id):
    return get_book(LIBRARY_PATH, book_id)


def _rewrite_image_srcs(
    html: str, book_id: int, chapter_index: int, chapter_dir: str
) -> str:
    pattern = re.compile(r'(<img\b[^>]*\bsrc=)(["\'])(.*?)\2', re.IGNORECASE)

    def replace(match):
        prefix, quote, src = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        resolved = resolve_relative_path(chapter_dir, src)
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


# Filters tied to fixed Book fields — always available regardless of library.
BUILTIN_FILTER_FIELDS = [
    {"key": "tags", "label": "Tags", "type": "multi"},
    {"key": "author", "label": "Author", "type": "single"},
    {"key": "series", "label": "Series", "type": "single"},
    {"key": "publisher", "label": "Publisher", "type": "single"},
]

BUILTIN_FILTER_GETTERS = {
    "tags": lambda book: book.tags,
    "author": lambda book: book.authors,
    "series": lambda book: [book.series] if book.series else [],
    "publisher": lambda book: [book.publisher] if book.publisher else [],
}

CUSTOM_FILTER_KEY_PREFIX = "custom:"


def _custom_filter_fields():
    """
    Filters discovered from the connected Calibre library's custom columns.
    Unlike BUILTIN_FILTER_FIELDS, this set varies per library, so it's
    recomputed from the live schema rather than hard-coded.
    """
    return [
        {
            "key": CUSTOM_FILTER_KEY_PREFIX + column["label"],
            "label": column["name"],
            "type": "multi" if column["is_multiple"] else "single",
        }
        for column in get_custom_columns(LIBRARY_PATH)
    ]


def _all_filter_fields():
    return BUILTIN_FILTER_FIELDS + _custom_filter_fields()


def _filter_values(book, field):
    if field["key"] in BUILTIN_FILTER_GETTERS:
        return BUILTIN_FILTER_GETTERS[field["key"]](book)
    custom_label = field["key"][len(CUSTOM_FILTER_KEY_PREFIX) :]
    return book.custom.get(custom_label, [])


def _enabled_filter_fields():
    enabled = set(get_settings(DB_PATH).enabled_filter_keys())
    return [field for field in _all_filter_fields() if field["key"] in enabled]


def _filter_options(books, field):
    values = set()
    for book in books:
        values.update(_filter_values(book, field))
    return sorted(values)


def _book_matches_filters(book, filter_fields, selected):
    for field in filter_fields:
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


def _build_history_entries(rows):
    """Turn (book_id, Position) pairs into display dicts, resolving chapter
    titles and skipping books/chapters that no longer exist."""
    entries = []
    for book_id, pos in rows:
        book = _get_calibre_book(book_id)
        if book is None:
            continue
        try:
            epub_book = parse_book(book.epub_path)
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


@app.route("/")
def library_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    view = request.args.get("view", "list")
    if view not in ("list", "grid"):
        view = "list"

    filter_fields = _enabled_filter_fields()
    selected = {}
    for field in filter_fields:
        if field["type"] == "multi":
            values = request.args.getlist(field["key"])
        else:
            value = request.args.get(field["key"], "").strip()
            values = [value] if value else []
        if values:
            selected[field["key"]] = values

    all_books = list_books(LIBRARY_PATH)
    total_library = len(all_books)
    filter_options = {
        field["key"]: _filter_options(all_books, field) for field in filter_fields
    }

    if query:
        all_books = [b for b in all_books if _book_matches(b, query)]
    all_books = [
        b for b in all_books if _book_matches_filters(b, filter_fields, selected)
    ]
    all_books.sort(key=lambda b: b.title.lower())

    total_pages = max(1, (len(all_books) + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * BOOKS_PER_PAGE
    books = all_books[start : start + BOOKS_PER_PAGE]

    positions = {book.id: get_position(DB_PATH, book.id) for book in books}
    positions = {k: v for k, v in positions.items() if v is not None}

    recent_limit = get_settings(DB_PATH).recent_list_limit
    recent = _build_history_entries(get_recent_positions(DB_PATH, recent_limit))

    # Active filter/query params, reusable for building pagination links that
    # preserve the current search+filter+view state.
    page_args = {"q": query} if query else {}
    for key, values in selected.items():
        page_args[key] = values
    if view != "list":
        page_args["view"] = view

    # Same as page_args but with the view flipped, for the display-toggle link.
    toggle_view = "grid" if view == "list" else "list"
    toggle_view_args = {k: v for k, v in page_args.items() if k != "view"}
    if toggle_view != "list":
        toggle_view_args["view"] = toggle_view

    return render_template(
        "library.html",
        books=books,
        positions=positions,
        recent=recent,
        page=page,
        total_pages=total_pages,
        query=query,
        filter_fields=filter_fields,
        filter_options=filter_options,
        selected=selected,
        page_args=page_args,
        view=view,
        toggle_view_args=toggle_view_args,
        total_matches=len(all_books),
        total_library=total_library,
    )


@app.route("/history")
def history_page():
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    total = count_positions(DB_PATH)
    total_pages = max(1, (total + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    offset = (page - 1) * BOOKS_PER_PAGE
    rows = get_positions_page(DB_PATH, BOOKS_PER_PAGE, offset)
    entries = _build_history_entries(rows)

    return render_template(
        "history.html",
        entries=entries,
        page=page,
        total_pages=total_pages,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>")
def read_chapter(book_id, chapter_index):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        abort(404, description="That book doesn't exist in your library.")

    epub_book = parse_book(calibre_book.epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        abort(404, description="That chapter doesn't exist.")

    chapter = epub_book.chapters[chapter_index]

    prev_index = chapter_index - 1 if chapter_index > 0 else None
    next_index = (
        chapter_index + 1 if chapter_index < len(epub_book.chapters) - 1 else None
    )

    saved = get_position(DB_PATH, book_id)
    known_updated_at = saved.updated_at if saved is not None else None

    return render_template(
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
        abort(404, description="That book doesn't exist in your library.")

    details = get_book_details(LIBRARY_PATH, book_id)
    if details is None:
        abort(404, description="That book doesn't exist in your library.")

    # The epub itself might be corrupt/missing even though Calibre has a
    # record of it — this page should still render the Calibre-side info
    # rather than 500ing, so chapter/technical data degrades gracefully.
    try:
        epub_book = parse_book(calibre_book.epub_path)
        stats = get_epub_stats(calibre_book.epub_path, epub_book)
        parse_error = None
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        epub_book, stats, parse_error = None, None, str(e)

    has_cover = find_cover_image(calibre_book.epub_path) is not None

    position = get_position(DB_PATH, book_id)
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

    return render_template(
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
    epub_path = get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        abort(404, description="That book doesn't exist in your library.")

    cover_path = find_cover_image(epub_path)
    if cover_path is None:
        abort(404, description="No cover image for that book.")

    return send_file(cover_path, mimetype="image/jpeg")


@app.route("/read/<int:book_id>/<int:chapter_index>/frame")
def read_chapter_frame(book_id, chapter_index):
    epub_path = get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        abort(404, description="That book doesn't exist in your library.")

    epub_book = parse_book(epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        abort(404, description="That chapter doesn't exist.")

    content = get_chapter_content(epub_path, epub_book, chapter_index)
    chapter_dir = get_chapter_dir(epub_book, epub_book.chapters[chapter_index])
    content = _rewrite_image_srcs(content, book_id, chapter_index, chapter_dir)
    css = get_stylesheets(epub_path, epub_book)

    return render_template(
        "chapter_frame.html",
        css=css,
        content=content,
        chapter_title=epub_book.chapters[chapter_index].title,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>/image/<path:img_path>")
def read_chapter_image(book_id, chapter_index, img_path):
    epub_path = get_book_epub_path(LIBRARY_PATH, book_id)
    if epub_path is None:
        abort(404)

    try:
        with zipfile.ZipFile(epub_path) as zf:
            data = zf.read(img_path)
    except KeyError:
        abort(404)

    mime_type, _ = mimetypes.guess_type(img_path)
    return Response(data, mimetype=mime_type or "application/octet-stream")


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        theme = request.form.get("theme", "").strip()
        font_family_key = request.form.get("font_family", "").strip()
        font_size_raw = request.form.get("font_size", "")
        override_epub_font = request.form.get("override_epub_font") is not None
        reader_font_family_key = request.form.get("reader_font_family", "").strip()
        reader_font_size_raw = request.form.get("reader_font_size", "")
        content_max_width_pct_raw = request.form.get("content_max_width_pct", "")
        recent_list_limit_raw = request.form.get("recent_list_limit", "")
        enabled_filters = request.form.getlist("enabled_filters")

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

        try:
            recent_list_limit = (
                int(recent_list_limit_raw) if recent_list_limit_raw else None
            )
        except ValueError:
            return "Invalid recent list limit", 400

        custom_colors = {
            field: request.form.get(field, "").strip() for field in CUSTOM_COLOR_FIELDS
        }

        valid_filter_keys = {field["key"] for field in _all_filter_fields()}
        invalid_filter_keys = set(enabled_filters) - valid_filter_keys
        if invalid_filter_keys:
            return (
                f"Invalid filter key(s): {', '.join(sorted(invalid_filter_keys))}",
                400,
            )

        try:
            save_settings(
                DB_PATH,
                theme=theme,
                font_family_key=font_family_key,
                font_size=font_size,
                override_epub_font=override_epub_font,
                reader_font_family_key=reader_font_family_key or None,
                reader_font_size=reader_font_size,
                content_max_width_pct=content_max_width_pct,
                recent_list_limit=recent_list_limit,
                custom_colors=custom_colors,
                enabled_filters=enabled_filters,
            )
        except ValueError as e:
            return str(e), 400

        return redirect(url_for("library_list"))

    return render_template(
        "settings.html",
        theme_choices=THEME_CHOICES,
        font_choices=FONT_CHOICES,
        filter_fields=_all_filter_fields(),
    )


@app.route("/settings/theme", methods=["POST"])
def update_theme():
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "").strip()
    if theme not in ("light", "dark"):  # custom isn't reachable from the quick toggle
        return jsonify({"error": "invalid theme"}), 400

    current = get_settings(DB_PATH)
    save_settings(
        DB_PATH,
        theme=theme,
        font_family_key=current.font_family_key,
        font_size=current.font_size,
    )
    return jsonify({"status": "ok"})


@app.route("/settings/reset-progress", methods=["POST"])
def reset_progress():
    reset_all_positions(DB_PATH)
    return redirect(url_for("settings_page"))


@app.route("/settings/clear-recent", methods=["POST"])
def clear_recent():
    clear_all_positions(DB_PATH)
    return redirect(url_for("settings_page"))


@app.route("/api/position/<int:book_id>", methods=["GET", "POST"])
def position_api(book_id):
    if request.method == "GET":
        saved = get_position(DB_PATH, book_id)
        if saved is None:
            return jsonify(None)
        return jsonify(
            {"chapter_index": saved.chapter_index, "updated_at": saved.updated_at}
        )

    data = request.get_json(silent=True) or {}
    chapter_index = data.get("chapter_index")

    if chapter_index is None:
        return jsonify({"error": "chapter_index is required"}), 400

    saved = save_position(DB_PATH, book_id, int(chapter_index))
    return jsonify(
        {"chapter_index": saved.chapter_index, "updated_at": saved.updated_at}
    )


@app.errorhandler(404)
def not_found(error):
    message = getattr(error, "description", None)
    return render_template("404.html", message=message), 404


@app.errorhandler(500)
def server_error(error):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
