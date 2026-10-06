from __future__ import annotations

import sqlite3
from pathlib import Path

from app.utils.logging import get_logger

log = get_logger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'local',
    source_path TEXT NOT NULL,
    rel_path TEXT,
    url TEXT,
    duration REAL NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    thumbnail TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_sec REAL NOT NULL DEFAULT 0,
    end_sec REAL NOT NULL DEFAULT 0,
    aspect TEXT NOT NULL DEFAULT '9:16',
    timeline_start REAL NOT NULL DEFAULT 0,
    timeline_end REAL NOT NULL DEFAULT 0,
    "order" INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS editor_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL,
    start_sec REAL NOT NULL DEFAULT 0,
    end_sec REAL NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    "order" INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL UNIQUE,
    language TEXT NOT NULL DEFAULT '',
    segments TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL UNIQUE,
    keyframes TEXT NOT NULL DEFAULT '[]',
    detector TEXT NOT NULL DEFAULT '',
    frames INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (media_id) REFERENCES media(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clips_media ON clips(media_id);
CREATE INDEX IF NOT EXISTS idx_captions_clip ON captions(clip_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_media ON media_transcripts(media_id);
CREATE INDEX IF NOT EXISTS idx_tracks_media ON media_tracks(media_id);
"""


class ProjectDatabase:
    """SQLite database stored inside a single .clipper project file."""

    def __init__(self, file_path: Path | str):
        self.file_path = Path(file_path)
        self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.file_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)
            # Migrate existing clips table: add new columns if missing
            try:
                self._conn.execute(
                    "ALTER TABLE clips ADD COLUMN timeline_start REAL NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute(
                    "ALTER TABLE clips ADD COLUMN timeline_end REAL NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._conn.execute(
                    'ALTER TABLE clips ADD COLUMN "order" INTEGER NOT NULL DEFAULT 0'
                )
            except sqlite3.OperationalError:
                pass
            self._conn.commit()
            log.debug("opened project database %s", self.file_path)
        return self._conn

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchall()

    def query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchone()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            finally:
                self._conn = None
                log.debug("closed project database %s", self.file_path)
