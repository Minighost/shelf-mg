import sqlite3
from dataclasses import dataclass

DEFAULT_THEME = "dark"
DEFAULT_FONT_FAMILY = "serif"
DEFAULT_FONT_SIZE = 1.1  # rem
DEFAULT_CONTENT_MAX_WIDTH_PCT = 40  # % of viewport width, desktop only
DEFAULT_RECENT_LIST_LIMIT = 5  # books shown in the library's "Recently read" list
DEFAULT_LIBRARY_VIEW = "list"
LIBRARY_VIEW_CHOICES = ["list", "grid", "card"]

FONT_CHOICES = {
    "serif": "Georgia, 'Times New Roman', serif",
    "sans": "Arial, Helvetica, sans-serif",
    "verdana": "Verdana, Geneva, sans-serif",
}

THEME_CHOICES = ["light", "dark", "custom"]

CUSTOM_COLOR_FIELDS = [
    "custom_bg_color",
    "custom_surface_color",
    "custom_text_color",
    "custom_text_muted",
    "custom_border_color",
    "custom_accent_color",
    "custom_accent_hover",
]

# Used as starting values the first time a user opens the custom color
# pickers, so they aren't staring at a blank/black palette by default —
# mirrors the light theme as a sensible starting point.
DEFAULT_CUSTOM_COLORS = {
    "custom_bg_color": "#14161a",
    "custom_surface_color": "#1c1f24",
    "custom_text_color": "#e2e5e9",
    "custom_text_muted": "#8a919c",
    "custom_border_color": "#2c3036",
    "custom_accent_color": "#c4524f",
    "custom_accent_hover": "#d3665f",
}

DEFAULT_OVERRIDE_EPUB_FONT = False

# Library filters enabled by default for new/existing users. The full set of
# *available* filters (built-ins plus discovered Calibre custom columns) is
# computed live in app.py, since it depends on the connected library's
# schema — this module just stores whichever keys the user has enabled.
DEFAULT_ENABLED_FILTERS = "tags,author,series"


@dataclass
class Settings:
    theme: str
    font_family_key: str
    font_family_css: str
    font_size: float
    override_epub_font: bool
    reader_font_family_key: str
    reader_font_family_css: str
    reader_font_size: float
    content_max_width_pct: float
    recent_list_limit: int
    library_view: str
    custom_bg_color: str
    custom_surface_color: str
    custom_text_color: str
    custom_text_muted: str
    custom_border_color: str
    custom_accent_color: str
    custom_accent_hover: str
    enabled_filters: str

    def enabled_filter_keys(self) -> list[str]:
        return [key for key in self.enabled_filters.split(",") if key]


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            theme TEXT NOT NULL,
            font_family_key TEXT NOT NULL,
            font_size REAL NOT NULL
        )
    """)

    new_columns = CUSTOM_COLOR_FIELDS + [
        "override_epub_font",
        "reader_font_family_key",
        "reader_font_size",
        "content_max_width_pct",
        "recent_list_limit",
        "enabled_filters",
        "library_view",
    ]
    for field in new_columns:
        try:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {field} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists

    conn.commit()
    conn.close()


def get_settings(db_path: str) -> Settings:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT theme, font_family_key, font_size, override_epub_font, "
        "reader_font_family_key, reader_font_size, content_max_width_pct, "
        "recent_list_limit, enabled_filters, library_view, "
        + ", ".join(CUSTOM_COLOR_FIELDS)
        + " FROM settings WHERE id = 1"
    ).fetchone()
    conn.close()

    if row is None:
        return Settings(
            theme=DEFAULT_THEME,
            font_family_key=DEFAULT_FONT_FAMILY,
            font_family_css=FONT_CHOICES[DEFAULT_FONT_FAMILY],
            font_size=DEFAULT_FONT_SIZE,
            override_epub_font=DEFAULT_OVERRIDE_EPUB_FONT,
            reader_font_family_key=DEFAULT_FONT_FAMILY,
            reader_font_family_css=FONT_CHOICES[DEFAULT_FONT_FAMILY],
            reader_font_size=DEFAULT_FONT_SIZE,
            content_max_width_pct=DEFAULT_CONTENT_MAX_WIDTH_PCT,
            recent_list_limit=DEFAULT_RECENT_LIST_LIMIT,
            enabled_filters=DEFAULT_ENABLED_FILTERS,
            library_view=DEFAULT_LIBRARY_VIEW,
            **DEFAULT_CUSTOM_COLORS,
        )

    key = row["font_family_key"]
    reader_key = row["reader_font_family_key"] or DEFAULT_FONT_FAMILY
    custom_colors = {
        field: row[field] or DEFAULT_CUSTOM_COLORS[field]
        for field in CUSTOM_COLOR_FIELDS
    }

    return Settings(
        theme=row["theme"],
        font_family_key=key,
        font_family_css=FONT_CHOICES.get(key, FONT_CHOICES[DEFAULT_FONT_FAMILY]),
        font_size=row["font_size"],
        override_epub_font=bool(int(row["override_epub_font"] or 0)),
        reader_font_family_key=reader_key,
        reader_font_family_css=FONT_CHOICES.get(
            reader_key, FONT_CHOICES[DEFAULT_FONT_FAMILY]
        ),
        reader_font_size=float(row["reader_font_size"] or DEFAULT_FONT_SIZE),
        content_max_width_pct=float(
            row["content_max_width_pct"] or DEFAULT_CONTENT_MAX_WIDTH_PCT
        ),
        recent_list_limit=int(row["recent_list_limit"] or DEFAULT_RECENT_LIST_LIMIT),
        enabled_filters=row["enabled_filters"]
        if row["enabled_filters"] is not None
        else DEFAULT_ENABLED_FILTERS,
        library_view=row["library_view"] or DEFAULT_LIBRARY_VIEW,
        **custom_colors,
    )


def save_settings(
    db_path: str,
    theme: str,
    font_family_key: str,
    font_size: float,
    override_epub_font: bool | None = None,
    reader_font_family_key: str | None = None,
    reader_font_size: float | None = None,
    content_max_width_pct: float | None = None,
    recent_list_limit: int | None = None,
    custom_colors: dict | None = None,
    enabled_filters: list[str] | None = None,
    library_view: str | None = None,
) -> None:
    if theme not in THEME_CHOICES:
        raise ValueError(f"invalid theme: {theme}")
    if font_family_key not in FONT_CHOICES:
        raise ValueError(f"invalid font_family_key: {font_family_key}")
    font_size = max(0.7, min(2.5, font_size))

    existing = get_settings(db_path)

    override_epub_font = (
        override_epub_font if override_epub_font is not None else existing.override_epub_font
    )

    reader_font_family_key = reader_font_family_key or existing.reader_font_family_key
    if reader_font_family_key not in FONT_CHOICES:
        raise ValueError(f"invalid reader_font_family_key: {reader_font_family_key}")

    reader_font_size = (
        reader_font_size if reader_font_size is not None else existing.reader_font_size
    )
    reader_font_size = max(0.7, min(2.5, reader_font_size))

    content_max_width_pct = (
        content_max_width_pct
        if content_max_width_pct is not None
        else existing.content_max_width_pct
    )
    content_max_width_pct = max(20, min(80, content_max_width_pct))

    recent_list_limit = (
        recent_list_limit
        if recent_list_limit is not None
        else existing.recent_list_limit
    )
    recent_list_limit = max(1, min(20, int(recent_list_limit)))

    library_view = library_view or existing.library_view
    if library_view not in LIBRARY_VIEW_CHOICES:
        raise ValueError(f"invalid library_view: {library_view}")

    if enabled_filters is None:
        resolved_enabled_filters = existing.enabled_filters
    else:
        # This module doesn't know which keys are actually valid — that
        # depends on the connected Calibre library's schema, so callers
        # (app.py) are responsible for validating keys before calling in.
        deduped = dict.fromkeys(key for key in enabled_filters if key)
        resolved_enabled_filters = ",".join(deduped)

    custom_colors = custom_colors or {}
    resolved_colors = {}
    for field in CUSTOM_COLOR_FIELDS:
        value = custom_colors.get(field)
        if value:
            if not _is_valid_hex_color(value):
                raise ValueError(f"invalid color for {field}: {value}")
            resolved_colors[field] = value
        else:
            resolved_colors[field] = getattr(existing, field)

    columns = [
        "id",
        "theme",
        "font_family_key",
        "font_size",
        "override_epub_font",
        "reader_font_family_key",
        "reader_font_size",
        "content_max_width_pct",
        "recent_list_limit",
        "enabled_filters",
        "library_view",
    ] + CUSTOM_COLOR_FIELDS
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{col} = excluded.{col}" for col in columns if col != "id")

    values = [
        1,
        theme,
        font_family_key,
        font_size,
        int(override_epub_font),
        reader_font_family_key,
        reader_font_size,
        content_max_width_pct,
        recent_list_limit,
        resolved_enabled_filters,
        library_view,
    ] + [resolved_colors[field] for field in CUSTOM_COLOR_FIELDS]

    conn = sqlite3.connect(db_path)
    conn.execute(
        f"""
        INSERT INTO settings ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}
        """,
        values,
    )
    conn.commit()
    conn.close()


def _is_valid_hex_color(value: str) -> bool:
    if not value.startswith("#"):
        return False
    hex_part = value[1:]
    if len(hex_part) not in (3, 6):
        return False
    try:
        int(hex_part, 16)
        return True
    except ValueError:
        return False
