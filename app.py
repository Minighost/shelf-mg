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
from positions import init_db as init_positions_db, get_position, save_position
from settings import (
    init_db as init_settings_db,
    get_settings,
    save_settings,
    FONT_CHOICES,
    THEME_CHOICES,
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


@app.route("/")
def library_list():
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    all_books = list_books(LIBRARY_PATH)
    total_pages = max(1, (len(all_books) + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)

    start = (page - 1) * BOOKS_PER_PAGE
    books = all_books[start : start + BOOKS_PER_PAGE]

    positions = {book.id: get_position(DB_PATH, book.id) for book in books}
    positions = {k: v for k, v in positions.items() if v is not None}

    return render_template(
        "library.html",
        books=books,
        positions=positions,
        page=page,
        total_pages=total_pages,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>")
def read_chapter(book_id, chapter_index):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        return "Book not found", 404

    epub_book = parse_book(calibre_book.epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        return "Chapter not found", 404

    chapter = epub_book.chapters[chapter_index]

    prev_index = chapter_index - 1 if chapter_index > 0 else None
    next_index = (
        chapter_index + 1 if chapter_index < len(epub_book.chapters) - 1 else None
    )

    saved = get_position(DB_PATH, book_id)
    restore_scroll_percent = None
    if saved is not None and saved.chapter_index == chapter_index:
        restore_scroll_percent = saved.scroll_percent

    return render_template(
        "reader.html",
        book=calibre_book,
        chapter={"index": chapter.index, "title": chapter.title},
        chapters=epub_book.chapters,
        prev_index=prev_index,
        next_index=next_index,
        restore_scroll_percent=restore_scroll_percent,
    )


@app.route("/read/<int:book_id>/<int:chapter_index>/frame")
def read_chapter_frame(book_id, chapter_index):
    calibre_book = _get_calibre_book(book_id)
    if calibre_book is None:
        return "Book not found", 404

    epub_book = parse_book(calibre_book.epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        return "Chapter not found", 404

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

        try:
            font_size = float(font_size_raw)
        except ValueError:
            return "Invalid font size", 400

        try:
            save_settings(
                DB_PATH,
                theme=theme,
                font_family_key=font_family_key,
                font_size=font_size,
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
    if theme not in THEME_CHOICES:
        return jsonify({"error": "invalid theme"}), 400

    current = get_settings(DB_PATH)
    save_settings(
        DB_PATH,
        theme=theme,
        font_family_key=current.font_family_key,
        font_size=current.font_size,
    )
    return jsonify({"status": "ok"})


@app.route("/api/position/<int:book_id>", methods=["POST"])
def save_position_api(book_id):
    data = request.get_json(silent=True) or {}
    chapter_index = data.get("chapter_index")
    scroll_percent = data.get("scroll_percent")

    if chapter_index is None or scroll_percent is None:
        return jsonify({"error": "chapter_index and scroll_percent are required"}), 400

    scroll_percent = max(0.0, min(1.0, float(scroll_percent)))

    save_position(DB_PATH, book_id, int(chapter_index), scroll_percent)
    return "", 204


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
