import sqlite3
from dataclasses import dataclass

DEFAULT_THEME = "dark"
DEFAULT_FONT_FAMILY = "serif"
DEFAULT_FONT_SIZE = 1.1  # rem

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


@dataclass
class Settings:
    theme: str
    font_family_key: str
    font_family_css: str
    font_size: float
    custom_bg_color: str
    custom_surface_color: str
    custom_text_color: str
    custom_text_muted: str
    custom_border_color: str
    custom_accent_color: str
    custom_accent_hover: str


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

    # Migrate existing DBs that predate the custom-color columns. SQLite has
    # no "ADD COLUMN IF NOT EXISTS", so each ALTER is attempted individually
    # and a "duplicate column" failure is treated as "already migrated".
    for field in CUSTOM_COLOR_FIELDS:
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
        "SELECT theme, font_family_key, font_size, "
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
            **DEFAULT_CUSTOM_COLORS,
        )

    key = row["font_family_key"]
    custom_colors = {
        field: row[field] or DEFAULT_CUSTOM_COLORS[field]
        for field in CUSTOM_COLOR_FIELDS
    }

    return Settings(
        theme=row["theme"],
        font_family_key=key,
        font_family_css=FONT_CHOICES.get(key, FONT_CHOICES[DEFAULT_FONT_FAMILY]),
        font_size=row["font_size"],
        **custom_colors,
    )


def save_settings(
    db_path: str,
    theme: str,
    font_family_key: str,
    font_size: float,
    custom_colors: dict | None = None,
) -> None:
    if theme not in THEME_CHOICES:
        raise ValueError(f"invalid theme: {theme}")
    if font_family_key not in FONT_CHOICES:
        raise ValueError(f"invalid font_family_key: {font_family_key}")
    font_size = max(0.7, min(2.5, font_size))

    custom_colors = custom_colors or {}
    resolved_colors = {}
    for field in CUSTOM_COLOR_FIELDS:
        value = custom_colors.get(field)
        if value:
            if not _is_valid_hex_color(value):
                raise ValueError(f"invalid color for {field}: {value}")
            resolved_colors[field] = value
        else:
            # Preserve whatever's already saved rather than wiping it out
            # on every save (e.g. saving a font change shouldn't blank
            # out previously-chosen custom colors).
            existing = get_settings(db_path)
            resolved_colors[field] = getattr(existing, field)

    columns = ["id", "theme", "font_family_key", "font_size"] + CUSTOM_COLOR_FIELDS
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{col} = excluded.{col}" for col in columns if col != "id")

    values = [1, theme, font_family_key, font_size] + [
        resolved_colors[field] for field in CUSTOM_COLOR_FIELDS
    ]

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
