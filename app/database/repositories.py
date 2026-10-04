from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.database.database import ProjectDatabase
from app.models.clip import Clip, EditorSettings, ExportSettings
from app.models.media import MediaItem, MediaKind
from app.models.project import Project, utc_now
from app.utils.paths import PROJECT_EXTENSION, safe_name, to_portable
from app.utils.logging import get_logger

log = get_logger("repositories")


def _row_to_media(row: sqlite3.Row) -> MediaItem:
    return MediaItem(
        id=row["id"],
        name=row["name"],
        kind=MediaKind(row["kind"]),
        source_path=row["source_path"],
        rel_path=row["rel_path"],
        url=row["url"],
        duration=row["duration"],
        width=row["width"],
        height=row["height"],
        size_bytes=row["size_bytes"],
        thumbnail=row["thumbnail"],
        created_at=row["created_at"],
    )


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        media_id=row["media_id"],
        name=row["name"],
        start=row["start_sec"],
        end=row["end_sec"],
        aspect=row["aspect"],
        timeline_start=row["timeline_start"] or 0.0,
        timeline_end=row["timeline_end"] or 0.0,
        order=row["order"] or 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MediaRepository:
    def __init__(self, db: ProjectDatabase):
        self.db = db

    def list(self) -> list[MediaItem]:
        rows = self.db.query("SELECT * FROM media ORDER BY id ASC")
        return [_row_to_media(row) for row in rows]

    def add(self, item: MediaItem, base_dir: Path) -> MediaItem:
        rel = to_portable(item.source_path, base_dir)
        cursor = self.db.execute(
            """
            INSERT INTO media
                (name, kind, source_path, rel_path, url, duration, width,
                 height, size_bytes, thumbnail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.name,
                item.kind.value,
                item.source_path,
                rel,
                item.url,
                item.duration,
                item.width,
                item.height,
                item.size_bytes,
                item.thumbnail,
                item.created_at or utc_now(),
            ),
        )
        item.id = int(cursor.lastrowid)
        item.rel_path = rel
        log.info("media added: %s (id=%s)", item.name, item.id)
        return item

    def update(self, item: MediaItem, base_dir: Path) -> None:
        if item.id is None:
            raise ValueError("cannot update media without id")
        rel = to_portable(item.source_path, base_dir)
        self.db.execute(
            """
            UPDATE media SET name=?, kind=?, source_path=?, rel_path=?, url=?,
                duration=?, width=?, height=?, size_bytes=?, thumbnail=?
            WHERE id=?
            """,
            (
                item.name,
                item.kind.value,
                item.source_path,
                rel,
                item.url,
                item.duration,
                item.width,
                item.height,
                item.size_bytes,
                item.thumbnail,
                item.id,
            ),
        )

    def delete(self, media_id: int) -> None:
        self.db.execute("DELETE FROM media WHERE id=?", (media_id,))
        log.info("media deleted id=%s", media_id)


class ClipRepository:
    def __init__(self, db: ProjectDatabase):
        self.db = db

    def list(self) -> list[Clip]:
        rows = self.db.query("SELECT * FROM clips ORDER BY id ASC")
        return [_row_to_clip(row) for row in rows]

    def add(self, clip: Clip) -> Clip:
        now = utc_now()
        cursor = self.db.execute(
            """
            INSERT INTO clips (media_id, name, start_sec, end_sec, aspect,
                               timeline_start, timeline_end, "order",
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip.media_id,
                clip.name,
                clip.start,
                clip.end,
                clip.aspect,
                clip.timeline_start,
                clip.timeline_end,
                clip.order,
                clip.created_at or now,
                now,
            ),
        )
        clip.id = int(cursor.lastrowid)
        clip.created_at = clip.created_at or now
        clip.updated_at = now
        log.info("clip added: %s (id=%s)", clip.name, clip.id)
        return clip

    def update(self, clip: Clip) -> None:
        if clip.id is None:
            raise ValueError("cannot update clip without id")
        clip.updated_at = utc_now()
        self.db.execute(
            """
            UPDATE clips SET media_id=?, name=?, start_sec=?, end_sec=?,
                aspect=?, timeline_start=?, timeline_end=?, "order"=?,
                updated_at=? WHERE id=?
            """,
            (
                clip.media_id,
                clip.name,
                clip.start,
                clip.end,
                clip.aspect,
                clip.timeline_start,
                clip.timeline_end,
                clip.order,
                clip.updated_at,
                clip.id,
            ),
        )

    def delete(self, clip_id: int) -> None:
        self.db.execute("DELETE FROM clips WHERE id=?", (clip_id,))
        log.info("clip deleted id=%s", clip_id)


class SettingsRepository:
    def __init__(self, db: ProjectDatabase):
        self.db = db

    def _load_map(self, table: str) -> dict[str, str]:
        rows = self.db.query(f"SELECT key, value FROM {table}")
        return {row["key"]: row["value"] for row in rows}

    def _save_map(self, table: str, data: dict) -> None:
        self.db.execute(f"DELETE FROM {table}")
        for key, value in data.items():
            self.db.execute(
                f"INSERT INTO {table} (key, value) VALUES (?, ?)",
                (key, value if isinstance(value, str) else json.dumps(value)),
            )

    def load_editor_settings(self) -> EditorSettings:
        data = self._load_map("editor_settings")
        parsed: dict = {}
        for key, value in data.items():
            parsed[key] = json.loads(value) if _is_json(value) else value
        return EditorSettings(**parsed)

    def save_editor_settings(self, settings: EditorSettings) -> None:
        payload = settings.model_dump()
        self._save_map(
            "editor_settings",
            {key: json.dumps(value) for key, value in payload.items()},
        )

    def load_export_settings(self) -> ExportSettings:
        data = self._load_map("export_settings")
        parsed: dict = {}
        for key, value in data.items():
            parsed[key] = json.loads(value) if _is_json(value) else value
        return ExportSettings(**parsed)

    def save_export_settings(self, settings: ExportSettings) -> None:
        payload = settings.model_dump()
        self._save_map(
            "export_settings",
            {key: json.dumps(value) for key, value in payload.items()},
        )


def _is_json(value: str) -> bool:
    if not value:
        return False
    return value[0] in "{[\"0123456789tfn-"


class ProjectRepository:
    """Loads and saves a complete Project from/to a .clipper SQLite file."""

    def __init__(self, file_path: Path | str):
        self.file_path = Path(file_path)
        if self.file_path.suffix.lower() != PROJECT_EXTENSION:
            self.file_path = self.file_path.with_suffix(PROJECT_EXTENSION)
        self.db = ProjectDatabase(self.file_path)
        self.media_repo = MediaRepository(self.db)
        self.clip_repo = ClipRepository(self.db)
        self.settings_repo = SettingsRepository(self.db)

    def load(self) -> Project:
        base = self.file_path.parent
        name_row = self.db.query_one(
            "SELECT value FROM project_meta WHERE key='name'"
        )
        created_row = self.db.query_one(
            "SELECT value FROM project_meta WHERE key='created_at'"
        )
        modified_row = self.db.query_one(
            "SELECT value FROM project_meta WHERE key='modified_at'"
        )
        name = name_row["value"] if name_row else self.file_path.stem
        project = Project(
            name=name,
            file_path=str(self.file_path),
            created_at=created_row["value"] if created_row else utc_now(),
            modified_at=modified_row["value"] if modified_row else utc_now(),
        )
        project.media = self.media_repo.list()
        for item in project.media:
            resolved = item.resolve_path(base)
            item.source_path = str(resolved)
        project.clips = self.clip_repo.list()
        project.export_settings = self.settings_repo.load_export_settings()
        project.editor_settings = self.settings_repo.load_editor_settings()
        log.info(
            "project loaded: %s (%d media, %d clips)",
            project.name,
            len(project.media),
            len(project.clips),
        )
        return project

    def save(self, project: Project) -> None:
        project.touch()
        meta = {
            "name": project.name,
            "created_at": project.created_at,
            "modified_at": project.modified_at,
            "schema_version": "1",
        }
        for key, value in meta.items():
            self.db.execute(
                """
                INSERT INTO project_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
        base = project.directory
        for item in project.media:
            if item.id is None:
                self.media_repo.add(item, base)
            else:
                self.media_repo.update(item, base)
        for clip in project.clips:
            if clip.id is None:
                self.clip_repo.add(clip)
            else:
                self.clip_repo.update(clip)
        self.settings_repo.save_editor_settings(project.editor_settings)
        self.settings_repo.save_export_settings(project.export_settings)
        log.info("project saved: %s", project.file_path)

    def close(self) -> None:
        self.db.close()


def new_project_file(workspace: Path, name: str) -> Path:
    return Path(workspace) / f"{safe_name(name)}{PROJECT_EXTENSION}"


__all__ = [
    "ClipRepository",
    "MediaRepository",
    "ProjectRepository",
    "SettingsRepository",
    "new_project_file",
]