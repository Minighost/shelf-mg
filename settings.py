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

THEME_CHOICES = ["light", "dark"]


@dataclass
class Settings:
    theme: str
    font_family_key: str
    font_family_css: str
    font_size: float


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
    conn.commit()
    conn.close()


def get_settings(db_path: str) -> Settings:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT theme, font_family_key, font_size FROM settings WHERE id = 1"
    ).fetchone()
    conn.close()

    if row is None:
        return Settings(
            theme=DEFAULT_THEME,
            font_family_key=DEFAULT_FONT_FAMILY,
            font_family_css=FONT_CHOICES[DEFAULT_FONT_FAMILY],
            font_size=DEFAULT_FONT_SIZE,
        )

    key = row["font_family_key"]
    return Settings(
        theme=row["theme"],
        font_family_key=key,
        font_family_css=FONT_CHOICES.get(key, FONT_CHOICES[DEFAULT_FONT_FAMILY]),
        font_size=row["font_size"],
    )


def save_settings(
    db_path: str, theme: str, font_family_key: str, font_size: float
) -> None:
    if theme not in THEME_CHOICES:
        raise ValueError(f"invalid theme: {theme}")
    if font_family_key not in FONT_CHOICES:
        raise ValueError(f"invalid font_family_key: {font_family_key}")
    font_size = max(0.7, min(2.5, font_size))

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO settings (id, theme, font_family_key, font_size)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            theme = excluded.theme,
            font_family_key = excluded.font_family_key,
            font_size = excluded.font_size
    """,
        (theme, font_family_key, font_size),
    )
    conn.commit()
    conn.close()
