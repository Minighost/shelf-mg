import zipfile
from xml.etree import ElementTree as ET
from dataclasses import dataclass

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
    href: str  # path inside the zip
    title: str  # human-readable title for the selector, exactly as the EPUB declares it


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


def parse_book(epub_path: str) -> Book:
    """
    Parse an EPUB file and return its spine (chapter order) plus titles.
    Does NOT extract chapter body content yet — see get_chapter_content().
    """
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

        if nav_hrefs_titles:
            chapters = [
                Chapter(index=i, href=href, title=title)
                for i, (href, title) in enumerate(nav_hrefs_titles)
            ]
        else:
            # Fallback: no nav data at all (rare, malformed EPUB). Use the
            # raw spine so the book is still readable, just with generic titles.
            chapters = [
                Chapter(index=i, href=href, title=f"Chapter {i + 1}")
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
        return Book(title=title, opf_path=opf_path, opf_dir=opf_dir, chapters=chapters)


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


def _extract_body_inner(raw_xhtml: str) -> str:
    """
    Pull the content inside <body>...</body>, dropping the outer
    <html>/<head> wrapper — that's the piece that actually goes into
    the reader page's isolated container.
    """
    import re

    match = re.search(r"<body[^>]*>(.*)</body>", raw_xhtml, re.DOTALL)
    return match.group(1) if match else raw_xhtml


def get_chapter_content(epub_path: str, book: Book, chapter_index: int) -> str:
    """
    Return the body HTML for one chapter, by index into book.chapters.
    This is what gets dropped into the isolated iframe/container.
    """
    chapter = book.chapters[chapter_index]
    with zipfile.ZipFile(epub_path) as zf:
        full_href = book.opf_dir + chapter.href
        raw = zf.read(full_href).decode("utf-8")
    return _extract_body_inner(raw)
