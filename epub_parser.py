import logging
import os
import zipfile
from xml.etree import ElementTree as ET
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- XML namespaces ---
# EPUB's internal XML files (content.opf, nav.xhtml, toc.ncx) mix
# elements from several XML vocabularies in one document — e.g. <dc:title>
# is defined by the Dublin Core metadata standard, <opf:manifest> by the
# EPUB packaging standard itself. The prefix (dc:, opf:) just distinguishes
# which vocabulary an element belongs to, in case two standards happen to
# define a tag with the same name.
#
# Python's ElementTree needs the full namespace URL to find these elements,
# not just the short prefix — so this dict maps "short prefix I want to
# type" -> "actual namespace URL", letting queries like find(".//dc:title")
# work instead of having to write out the full URL every time.
NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}


@dataclass
class Chapter:
    index: int  # 0-based position in the nav-declared chapter list
    starting_href: str  # path inside the zip to the file the nav points at
    title: str  # human-readable title for the selector, exactly as the EPUB declares it
    all_hrefs: list[str]  # every spine file holding this chapter, in reading
    # order, starting_href first — Calibre splits long
    # chapters across several files (see parse_book)


@dataclass
class Book:
    title: str
    opf_path: str  # path to content.opf inside the zip
    opf_dir: str  # directory containing content.opf (hrefs are relative to this)
    chapters: list[Chapter]


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    """META-INF/container.xml tells us where content.opf actually lives."""
    container_xml = zf.read("META-INF/container.xml")
    root = ET.fromstring(container_xml)
    rootfile = root.find(".//container:rootfile", NS)
    return rootfile.attrib["full-path"]


def _opf_dir(opf_path: str) -> str:
    """Directory containing the OPF file — chapter hrefs are relative to this."""
    if "/" in opf_path:
        return opf_path.rsplit("/", 1)[0] + "/"
    return ""


# Module-level cache: epub_path -> (mtime_at_cache_time, Book). One entry
# per book ever opened this session — a Book here is just chapter titles/
# hrefs, not chapter content, so caching every book in the library
# simultaneously costs very little memory. Invalidated per-file: if a
# specific EPUB is replaced on disk, only that book's cache entry goes
# stale, not the whole cache.
_parse_book_cache: dict[str, tuple[float, "Book"]] = {}


def parse_book(epub_path: str) -> Book:
    """
    Parse an EPUB file and return its spine (chapter order) plus titles.
    Does NOT extract chapter body content yet — see get_chapter_content().

    Cached per epub_path, invalidated automatically whenever that specific
    file's mtime changes.
    """
    current_mtime = os.path.getmtime(epub_path)

    cached = _parse_book_cache.get(epub_path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    logger.debug("cache miss for %s, parsing", epub_path)

    with zipfile.ZipFile(epub_path) as zf:
        opf_path = _find_opf_path(zf)
        opf_dir = _opf_dir(opf_path)
        opf_xml = ET.fromstring(zf.read(opf_path))

        # --- title ---
        title_el = opf_xml.find(".//dc:title", NS)
        title = title_el.text if title_el is not None else "Untitled"

        # --- manifest: id -> href, so spine idrefs can be resolved to files ---
        manifest = {}
        for item in opf_xml.findall(".//opf:manifest/opf:item", NS):
            manifest[item.attrib["id"]] = item.attrib["href"]

        # --- spine: full reading order (includes non-chapter fragments) ---
        spine_ids = [
            itemref.attrib["idref"]
            for itemref in opf_xml.findall(".//opf:spine/opf:itemref", NS)
        ]
        spine_hrefs_in_order = [manifest[i] for i in spine_ids if i in manifest]

        # --- nav: the SOURCE OF TRUTH for what counts as a chapter ---
        # Why not just walk the spine: real EPUBs can contain spine items
        # that aren't "chapters" a human would recognize (duplicate/orphaned
        # preface fragments, split artifacts from conversion tools, etc.).
        # The nav document is where the format itself declares "here are the
        # actual navigable sections" — so we trust it over guessing from the
        # raw spine order.
        nav_hrefs_titles = _parse_nav(zf, opf_dir, opf_xml)

        # The nav document's own href (EPUB3's nav.xhtml) must never be
        # swept into a chapter's span — it sits right after chapter 0 in
        # the spine (see _group_spine_by_chapter).
        nav_item = opf_xml.find(".//opf:manifest/opf:item[@properties='nav']", NS)
        nav_doc_href = nav_item.attrib["href"] if nav_item is not None else None

        if nav_hrefs_titles:
            nav_hrefs = [href for href, _ in nav_hrefs_titles]
            hrefs_by_chapter = _group_spine_by_chapter(
                spine_hrefs_in_order, nav_hrefs, nav_doc_href
            )
            chapters = [
                Chapter(
                    index=i,
                    starting_href=href,
                    title=title,
                    all_hrefs=hrefs_by_chapter[i],
                )
                for i, (href, title) in enumerate(nav_hrefs_titles)
            ]
        else:
            # Fallback: no nav data at all (rare, malformed EPUB). Use the
            # raw spine so the book is still readable, just with generic titles.
            chapters = [
                Chapter(
                    index=i,
                    starting_href=href,
                    title=f"Chapter {i + 1}",
                    all_hrefs=[href],
                )
                for i, href in enumerate(spine_hrefs_in_order)
                if not href.endswith("nav.xhtml")
            ]

        # Note: we deliberately do NOT fold "Preface"/"Afterword" nav entries
        # into adjacent chapters here, even though that's how AO3 fics display
        # on AO3 itself. Special-casing one source's naming convention doesn't
        # belong in a parser meant to stay generic across EPUB sources — the
        # chapter list here is exactly what the EPUB's own nav declares.
        # If you want AO3-style folded display, that's a display-layer
        # decision to make later, not something to bake in here.
        book = Book(title=title, opf_path=opf_path, opf_dir=opf_dir, chapters=chapters)

    _parse_book_cache[epub_path] = (current_mtime, book)
    return book


def _parse_nav(zf: zipfile.ZipFile, opf_dir: str, opf_xml) -> list[tuple[str, str]]:
    """
    Returns [(href, title), ...] in nav order, from whichever nav document
    exists. Tries EPUB3's nav.xhtml first, falls back to EPUB2's toc.ncx.
    This defines the actual chapter list — see comment in parse_book().
    """
    # Try EPUB3 nav document (properties="nav" in the manifest)
    nav_item = opf_xml.find(".//opf:manifest/opf:item[@properties='nav']", NS)
    if nav_item is not None:
        nav_href = opf_dir + nav_item.attrib["href"]
        try:
            nav_xml = ET.fromstring(zf.read(nav_href))
            result = []
            for a in nav_xml.findall(".//xhtml:nav//xhtml:a", NS):
                href = a.attrib.get("href", "").split("#")[0]
                if href and a.text:
                    result.append((href, a.text.strip()))
            if result:
                return result
        except KeyError:
            pass  # nav file listed in manifest but missing from zip

    # Fall back to EPUB2 toc.ncx
    ncx_item = opf_xml.find(".//opf:manifest/opf:item[@id='ncx']", NS)
    if ncx_item is not None:
        ncx_href = opf_dir + ncx_item.attrib["href"]
        try:
            ncx_xml = ET.fromstring(zf.read(ncx_href))
            result = []
            for navpoint in ncx_xml.findall(".//ncx:navPoint", NS):
                label = navpoint.find(".//ncx:text", NS)
                content = navpoint.find("ncx:content", NS)
                if label is not None and content is not None:
                    href = content.attrib.get("src", "").split("#")[0]
                    if href and label.text:
                        result.append((href, label.text.strip()))
            return result
        except KeyError:
            pass

    return []


def _group_spine_by_chapter(
    spine_hrefs: list[str], chapter_hrefs: list[str], nav_doc_href: str | None
) -> list[list[str]]:
    """
    Calibre splits long chapters across several spine files but points the
    nav at only the first, so reading just that one file silently truncates
    the chapter. A chapter's true span is its own spine position up to the
    *next greater* (not just the next list entry) chapter position —
    computed per-chapter, so two nav entries pointing to the same file both
    get that file's real span instead of one of them getting an empty one.

    Falls back to a single-file span whenever a chapter's href can't be
    located in the spine, or would resolve to an empty/backwards span.
    """

    positions = []
    for href in chapter_hrefs:
        try:
            positions.append(spine_hrefs.index(href))
        except ValueError:
            positions.append(None)

    groups = []
    for href, pos in zip(chapter_hrefs, positions):
        if pos is None:
            groups.append([href])
            continue

        later_positions = [p for p in positions if p is not None and p > pos]
        end = min(later_positions) if later_positions else len(spine_hrefs)

        span = [
            h
            for h in spine_hrefs[pos:end]
            if h != nav_doc_href and not h.endswith("nav.xhtml")
        ]
        groups.append(span if span else [href])

    return groups


def _split_body(raw_xhtml: str) -> tuple[dict[str, str], str]:
    """
    Split <body ...>...</body> into its attributes and its inner HTML,
    dropping the outer <html>/<head> wrapper.

    The attributes matter as much as the content: Calibre-converted EPUBs put
    <body class="calibre">, and .calibre carries the page's real margins/font
    sizing. Discarding it (as this used to) silently dropped that styling.
    """
    import re

    match = re.search(r"<body([^>]*)>(.*)</body>", raw_xhtml, re.DOTALL)
    if match is None:
        return {}, raw_xhtml

    # group(1) is the raw text between "<body" and ">", e.g. ` class="calibre"
    # xml:lang="en"`. Each findall match is one name="value" pair, captured as
    # (name, value) and collected into a dict:
    #   [\w:-]+   attribute name; ':' and '-' allow xml:lang, data-foo
    #   ["']      opening quote, then [^"']* value up to the closing one
    # Only quoted values are matched. Bare attributes (<body hidden>) are
    # dropped rather than mishandled — no EPUB body relies on one, and the
    # frame template renders these as key="value" pairs anyway.
    attrs = dict(re.findall(r"""([\w:-]+)\s*=\s*["']([^"']*)["']""", match.group(1)))
    return attrs, match.group(2)


def get_chapter_content(epub_path: str, book: Book, chapter_index: int) -> str:
    """Body HTML only — for callers that just want the text, e.g. search indexing."""
    return get_chapter_body(epub_path, book, chapter_index)[1]


def get_chapter_body(
    epub_path: str, book: Book, chapter_index: int
) -> tuple[dict[str, str], str]:
    """
    Return one chapter's <body> attributes and inner HTML, by index into
    book.chapters. This is what gets dropped into the isolated iframe —
    the attributes go onto the frame's own <body> so the EPUB's body-level
    styling still applies (see _split_body).

    A chapter can span several spine files (chapter.all_hrefs) when Calibre
    has split it during conversion — their bodies are concatenated in order.
    <body> attributes come from the first file only; every file in a group
    shares identical attributes in practice (see _group_spine_by_chapter).
    """
    chapter = book.chapters[chapter_index]
    with zipfile.ZipFile(epub_path) as zf:
        attrs = None
        inner_parts = []
        for href in chapter.all_hrefs:
            full_href = book.opf_dir + href
            raw = zf.read(full_href).decode("utf-8")
            part_attrs, inner = _split_body(raw)
            if attrs is None:
                attrs = part_attrs
            inner_parts.append(inner)
    return attrs, "\n".join(inner_parts)


def resolve_relative_path(base_dir: str, relative_path: str) -> str:
    """
    Resolve a relative path (e.g. an <img src="../images/foo.png"> found
    inside a chapter) against the directory containing that chapter's own
    XHTML file, into a path relative to the EPUB zip root.
    """
    import posixpath

    return posixpath.normpath(posixpath.join(base_dir, relative_path))


def get_chapter_dir(book: Book, chapter: Chapter) -> str:
    """Directory (relative to zip root) containing a given chapter's XHTML file."""
    full_href = book.opf_dir + chapter.starting_href
    if "/" in full_href:
        return full_href.rsplit("/", 1)[0] + "/"
    return ""


def get_stylesheets(epub_path: str, book: Book) -> str:
    """
    Return the concatenated text of every CSS file declared in the EPUB's
    manifest. This is what goes inside the iframe alongside chapter content
    — the fic keeps its own original formatting, just isolated from the
    app's own CSS (see the .calibre/.calibre1/... classname collision
    findings from earlier testing for why isolation matters).
    """
    with zipfile.ZipFile(epub_path) as zf:
        opf_xml = ET.fromstring(zf.read(book.opf_path))
        css_hrefs = [
            item.attrib["href"]
            for item in opf_xml.findall(".//opf:manifest/opf:item", NS)
            if item.attrib.get("media-type") == "text/css"
        ]

        parts = []
        for href in css_hrefs:
            full_href = book.opf_dir + href
            try:
                parts.append(zf.read(full_href).decode("utf-8"))
            except KeyError:
                continue  # manifest lists it but it's missing from the zip — skip, don't crash
        return "\n".join(parts)


@dataclass
class EpubStats:
    file_size_bytes: int
    epub_version: str  # "EPUB 3" or "EPUB 2"
    spine_item_count: int
    image_count: int
    css_count: int
    manifest_item_count: int


def get_epub_stats(epub_path: str, book: Book) -> EpubStats:
    """
    Technical/file details for the inspection page. Re-reads the manifest
    directly rather than having parse_book() expose it, to keep parse_book's
    return shape unchanged for the routes that already depend on it.
    """
    file_size_bytes = os.path.getsize(epub_path)

    with zipfile.ZipFile(epub_path) as zf:
        opf_xml = ET.fromstring(zf.read(book.opf_path))

        manifest_items = opf_xml.findall(".//opf:manifest/opf:item", NS)
        manifest = {item.attrib["id"]: item.attrib for item in manifest_items}

        spine_ids = [
            itemref.attrib["idref"]
            for itemref in opf_xml.findall(".//opf:spine/opf:itemref", NS)
        ]

        # Same detection order as _parse_nav(): an EPUB3 nav document
        # (properties="nav") takes precedence; a toc.ncx item means EPUB2.
        # Kept in sync with _parse_nav so the version label always matches
        # which nav source parse_book() actually used for the chapter list.
        has_nav = (
            opf_xml.find(".//opf:manifest/opf:item[@properties='nav']", NS) is not None
        )
        has_ncx = opf_xml.find(".//opf:manifest/opf:item[@id='ncx']", NS) is not None
        epub_version = "EPUB 3" if has_nav else "EPUB 2" if has_ncx else "Unknown"

        image_count = sum(
            1
            for attrib in manifest.values()
            if attrib.get("media-type", "").startswith("image/")
        )
        css_count = sum(
            1 for attrib in manifest.values() if attrib.get("media-type") == "text/css"
        )

    return EpubStats(
        file_size_bytes=file_size_bytes,
        epub_version=epub_version,
        spine_item_count=len(spine_ids),
        image_count=image_count,
        css_count=css_count,
        manifest_item_count=len(manifest),
    )


def clear_cache() -> None:
    """Manually drop every cached parsed EPUB, regardless of mtime. See
    calibre_reader.clear_cache() for why this exists alongside automatic
    mtime invalidation."""
    _parse_book_cache.clear()
