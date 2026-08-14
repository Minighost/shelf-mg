import mimetypes
import os
import re
import zipfile

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    abort,
    redirect,
    url_for,
)

from calibre_reader import list_books
from epub_parser import (
    parse_book,
    get_chapter_content,
    get_stylesheets,
    get_chapter_dir,
    resolve_relative_path,
)
from positions import (
    init_db as init_positions_db,
    get_position,
    get_recent_positions,
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
    books = list_books(LIBRARY_PATH)
    return next((b for b in books if b.id == book_id), None)


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


@app.route("/")
def library_list():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    all_books = list_books(LIBRARY_PATH)
    if query:
        all_books = [b for b in all_books if _book_matches(b, query)]

    total_pages = max(1, (len(all_books) + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * BOOKS_PER_PAGE
    books = all_books[start : start + BOOKS_PER_PAGE]

    positions = {book.id: get_position(DB_PATH, book.id) for book in books}
    positions = {k: v for k, v in positions.items() if v is not None}

    recent_limit = get_settings(DB_PATH).recent_list_limit
    recent = []
    for recent_book_id, pos in get_recent_positions(DB_PATH, recent_limit):
        recent_book = _get_calibre_book(recent_book_id)
        if recent_book is None:
            continue
        try:
            recent_epub = parse_book(recent_book.epub_path)
            chapter_title = recent_epub.chapters[pos.chapter_index].title
        except (IndexError, OSError):
            continue
        recent.append(
            {
                "book": recent_book,
                "chapter_index": pos.chapter_index,
                "chapter_title": chapter_title,
                "updated_at": pos.updated_at,
            }
        )

    return render_template(
        "library.html",
        books=books,
        positions=positions,
        recent=recent,
        page=page,
        total_pages=total_pages,
        query=query,
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


@app.route("/read/<int:book_id>/<int:chapter_index>/frame")
def read_chapter_frame(book_id, chapter_index):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        abort(404, description="That book doesn't exist in your library.")

    epub_book = parse_book(calibre_book.epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        abort(404, description="That chapter doesn't exist.")

    content = get_chapter_content(calibre_book.epub_path, epub_book, chapter_index)
    chapter_dir = get_chapter_dir(epub_book, epub_book.chapters[chapter_index])
    content = _rewrite_image_srcs(content, book_id, chapter_index, chapter_dir)
    css = get_stylesheets(calibre_book.epub_path, epub_book)

    return render_template(
        "chapter_frame.html",
        css=css,
        content=content,
        chapter_title=epub_book.chapters[chapter_index].title,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>/image/<path:img_path>")
def read_chapter_image(book_id, chapter_index, img_path):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        abort(404)

    try:
        with zipfile.ZipFile(calibre_book.epub_path) as zf:
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
            )
        except ValueError as e:
            return str(e), 400

        return redirect(url_for("settings_page"))

    return render_template(
        "settings.html",
        theme_choices=THEME_CHOICES,
        font_choices=FONT_CHOICES,
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
    return jsonify({"chapter_index": saved.chapter_index, "updated_at": saved.updated_at})


@app.errorhandler(404)
def not_found(error):
    message = getattr(error, "description", None)
    return render_template("404.html", message=message), 404


@app.errorhandler(500)
def server_error(error):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
