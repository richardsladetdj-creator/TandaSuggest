"""Configurable genre classification settings."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class GenreRule:
    name: str
    partial_match: bool = True  # True = substring match, False = exact match

    def matches(self, raw_genre: str) -> bool:
        g = raw_genre.lower()
        n = self.name.lower()
        return (n in g) if self.partial_match else (n == g)

    def family(self) -> str:
        """Internal key used for genre_family column."""
        return self.name.lower()


@dataclass
class CortinaConfig:
    names: list[str] = field(default_factory=lambda: ["Cortina"])
    partial_match: bool = True
    catch_all: bool = False  # if True, any non-dance track → cortina

    def matches(self, raw_genre: str) -> bool:
        g = raw_genre.lower()
        for name in self.names:
            n = name.lower()
            if self.partial_match:
                if n in g:
                    return True
            else:
                if n == g:
                    return True
        return False


@dataclass
class GenreSettings:
    dance_genres: list[GenreRule]
    cortina: CortinaConfig


DEFAULT_SETTINGS = GenreSettings(
    dance_genres=[
        GenreRule("Tango", partial_match=True),
        GenreRule("Vals", partial_match=True),
        GenreRule("Milonga", partial_match=True),
    ],
    cortina=CortinaConfig(names=["Cortina"], partial_match=True, catch_all=False),
)


def classify_genre(raw_genre: str, settings: GenreSettings) -> str | None:
    """Map a raw genre string to a genre family key, or None.

    Replaces the hardcoded db.genre_family() function.
    Returns the dance genre family (lowercased name) if matched,
    "cortina" if matched as cortina or catch_all is on, else None.
    """
    if not raw_genre:
        return None

    for rule in settings.dance_genres:
        if rule.matches(raw_genre):
            return rule.family()

    if settings.cortina.catch_all:
        return "cortina"

    if settings.cortina.matches(raw_genre):
        return "cortina"

    return None


def is_relevant(raw_genre: str, settings: GenreSettings) -> bool:
    """Return True if a track with this genre should be imported."""
    if settings.cortina.catch_all:
        return True
    return classify_genre(raw_genre, settings) is not None


def load_settings(conn: sqlite3.Connection) -> GenreSettings:
    """Load settings from the database, falling back to defaults."""
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'genre_rules'"
        ).fetchone()
        cortina_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'cortina_config'"
        ).fetchone()
    except Exception:
        return DEFAULT_SETTINGS

    dance_genres = DEFAULT_SETTINGS.dance_genres
    cortina = DEFAULT_SETTINGS.cortina

    if row:
        try:
            rules_data = json.loads(row[0])
            dance_genres = [
                GenreRule(name=r["name"], partial_match=r["partial_match"])
                for r in rules_data
            ]
        except Exception:
            pass

    if cortina_row:
        try:
            c_data = json.loads(cortina_row[0])
            cortina = CortinaConfig(
                names=c_data["names"],
                partial_match=c_data["partial_match"],
                catch_all=c_data["catch_all"],
            )
        except Exception:
            pass

    return GenreSettings(dance_genres=dance_genres, cortina=cortina)


def save_settings(conn: sqlite3.Connection, settings: GenreSettings) -> None:
    """Persist settings to the database."""
    rules_json = json.dumps([
        {"name": r.name, "partial_match": r.partial_match}
        for r in settings.dance_genres
    ])
    cortina_json = json.dumps({
        "names": settings.cortina.names,
        "partial_match": settings.cortina.partial_match,
        "catch_all": settings.cortina.catch_all,
    })
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('genre_rules', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (rules_json,),
    )
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('cortina_config', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (cortina_json,),
    )
    conn.commit()
