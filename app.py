import os
from flask import Flask, render_template, request, jsonify

from calibre_reader import list_books
from epub_parser import parse_book, get_chapter_content
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


@app.route("/")
def library_list():
    books = list_books(LIBRARY_PATH)
    # Keyed by book id so the template can do positions.get(book.id) —
    # most books won't have a saved position, and that's fine, it just
    # means no "continue reading" link for that one.
    positions = {book.id: get_position(DB_PATH, book.id) for book in books}
    positions = {k: v for k, v in positions.items() if v is not None}
    return render_template("library.html", books=books, positions=positions)


@app.route("/read/<int:book_id>/<int:chapter_index>")
def read_chapter(book_id, chapter_index):
    calibre_books = list_books(LIBRARY_PATH)
    calibre_book = next((b for b in calibre_books if b.id == book_id), None)
    if calibre_book is None:
        return "Book not found", 404

    epub_book = parse_book(calibre_book.epub_path)
    if chapter_index < 0 or chapter_index >= len(epub_book.chapters):
        return "Chapter not found", 404

    chapter = epub_book.chapters[chapter_index]
    content = get_chapter_content(calibre_book.epub_path, epub_book, chapter_index)

    prev_index = chapter_index - 1 if chapter_index > 0 else None
    next_index = (
        chapter_index + 1 if chapter_index < len(epub_book.chapters) - 1 else None
    )

    # Only restore scroll if we're reopening the SAME chapter that was
    # saved — navigating to a different chapter always starts at the top,
    # by design (see the full-page-nav decision).
    saved = get_position(DB_PATH, book_id)
    restore_scroll_percent = None
    if saved is not None and saved.chapter_index == chapter_index:
        restore_scroll_percent = saved.scroll_percent

    return render_template(
        "reader.html",
        book=calibre_book,
        chapter={"index": chapter.index, "title": chapter.title, "content": content},
        chapters=epub_book.chapters,
        prev_index=prev_index,
        next_index=next_index,
        restore_scroll_percent=restore_scroll_percent,
    )


@app.route("/api/position/<int:book_id>", methods=["POST"])
def save_position_api(book_id):
    data = request.get_json(silent=True) or {}
    chapter_index = data.get("chapter_index")
    scroll_percent = data.get("scroll_percent")

    if chapter_index is None or scroll_percent is None:
        return jsonify({"error": "chapter_index and scroll_percent are required"}), 400

    # Clamp defensively — a bad scroll calculation on the client shouldn't
    # be able to write nonsense (negative, or way over 1.0) into storage.
    scroll_percent = max(0.0, min(1.0, float(scroll_percent)))

    save_position(DB_PATH, book_id, int(chapter_index), scroll_percent)
    return "", 204


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
