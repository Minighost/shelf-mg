import html
import logging
import re
import sqlite3
import threading

import calibre_reader
import epub_parser

logger = logging.getLogger(__name__)


def init_db(db_path: str) -> None:
    """Create the chapters_fts virtual table if it doesn't already exist. Safe to call every startup."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chapters_fts USING fts5(
            book_id UNINDEXED,
            chapter_index UNINDEXED,
            chapter_title,
            content
        )
    """)
    conn.commit()
    conn.close()


# NOTE: this entire module assumes gunicorn -w 1 (see app.py). _index_status/_index_lock
# are process-global; with more than one worker, each worker gets its own independent
# copy and the "is a reindex already running" check silently stops working across workers.
_index_lock = threading.Lock()
_index_status = {"running": False, "current": 0, "total": 0, "error": None}

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", raw_html))


def start_reindex(library_path: str, db_path: str) -> bool:
    """Atomically check-and-start a background full reindex. Returns False if one is already running."""
    with _index_lock:
        if _index_status["running"]:
            return False
        _index_status.update(running=True, current=0, total=0, error=None)
    thread = threading.Thread(
        target=_reindex_thread, args=(library_path, db_path), daemon=True
    )
    thread.start()
    return True


def get_status() -> dict:
    with _index_lock:
        return dict(_index_status)


def _reindex_thread(library_path: str, db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        books = calibre_reader.list_books(library_path)
        with _index_lock:
            _index_status["total"] = len(books)
        logger.info("reindex: starting, %d books", len(books))

        conn.execute("DELETE FROM chapters_fts")
        conn.commit()

        for i, book in enumerate(books):
            try:
                epub_book = epub_parser.parse_book(book.epub_path)
                rows = [
                    (
                        book.id,
                        chapter.index,
                        chapter.title,
                        _html_to_text(
                            epub_parser.get_chapter_content(
                                book.epub_path, epub_book, chapter.index
                            )
                        ),
                    )
                    for chapter in epub_book.chapters
                ]
                conn.executemany(
                    "INSERT INTO chapters_fts (book_id, chapter_index, chapter_title, content) "
                    "VALUES (?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            except Exception as e:
                logger.warning(
                    "reindex: failed on book id=%s title=%r: %s", book.id, book.title, e
                )
            with _index_lock:
                _index_status["current"] = i + 1

        logger.info("reindex: completed, %d books", len(books))
    except Exception as e:
        logger.error("reindex: job failed: %s", e)
        with _index_lock:
            _index_status["error"] = str(e)
    finally:
        conn.close()
        with _index_lock:
            _index_status["running"] = False
