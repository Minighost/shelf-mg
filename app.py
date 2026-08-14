import mimetypes
import os
import re
import zipfile

from flask import Flask, render_template, request, jsonify, Response, abort

from calibre_reader import list_books
from epub_parser import (
    parse_book,
    get_chapter_content,
    get_stylesheets,
    get_chapter_dir,
    resolve_relative_path,
)
from positions import init_db, get_position, save_position

app = Flask(__name__)

LIBRARY_PATH = os.environ.get("SHELF_MG_LIBRARY_PATH")
if not LIBRARY_PATH:
    raise RuntimeError(
        "SHELF_MG_LIBRARY_PATH environment variable is not set. "
        "Point it at your Calibre library folder (the one containing metadata.db)."
    )

DB_PATH = os.environ.get("SHELF_MG_DB_PATH", "shelf-mg.db")
init_db(DB_PATH)


def _get_calibre_book(book_id):
    """Shared lookup — every route below needs this, so it's factored out once."""
    books = list_books(LIBRARY_PATH)
    return next((b for b in books if b.id == book_id), None)


def _rewrite_image_srcs(
    html: str, book_id: int, chapter_index: int, chapter_dir: str
) -> str:
    """
    Rewrite <img src="..."> so it points at our image-serving route instead
    of a raw relative path inside the EPUB zip (which the browser has no
    way to resolve on its own). Absolute URLs and data: URIs are left alone.
    """
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
    books = list_books(LIBRARY_PATH)
    positions = {book.id: get_position(DB_PATH, book.id) for book in books}
    positions = {k: v for k, v in positions.items() if v is not None}
    return render_template("library.html", books=books, positions=positions)


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
    """
    The isolated document that goes inside the reader page's iframe.
    Carries the EPUB's own CSS, so the fic keeps looking like itself —
    just walled off from shelf-mg's own page styles in both directions.
    """
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
    """Serve one image file pulled directly out of the EPUB zip."""
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
