from __future__ import annotations

import json
from pathlib import Path

from app.database.repositories import ProjectRepository, new_project_file
from app.models.project import Project, RecentProject, utc_now
from app.utils.logging import get_logger
from app.utils.paths import PROJECT_EXTENSION, projects_dir, safe_name, unique_workspace

log = get_logger("project")

REGISTRY_FILE = "recent_projects.json"
MAX_RECENT = 12


class ProjectError(RuntimeError):
    pass


class ProjectService:
    """Project lifecycle: create, open, save, save-as, recent list."""

    def registry_path(self) -> Path:
        return projects_dir() / REGISTRY_FILE

    def _read_registry(self) -> list[dict]:
        path = self.registry_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("registry unreadable: %s", exc)
            return []
        return data if isinstance(data, list) else []

    def _write_registry(self, entries: list[dict]) -> None:
        path = self.registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entries[:MAX_RECENT], indent=2), encoding="utf-8"
        )

    def recent_projects(self) -> list[RecentProject]:
        result: list[RecentProject] = []
        for entry in self._read_registry():
            file_path = Path(entry.get("file_path", ""))
            result.append(
                RecentProject(
                    name=entry.get("name") or file_path.stem,
                    file_path=str(file_path),
                    modified_at=entry.get("modified_at") or "",
                    clip_count=int(entry.get("clip_count") or 0),
                    exists=file_path.exists(),
                )
            )
        return result

    def touch_recent(self, project: Project) -> None:
        entries = [
            e
            for e in self._read_registry()
            if Path(e.get("file_path", "")).resolve() != project.path.resolve()
        ]
        entries.insert(
            0,
            {
                "name": project.name,
                "file_path": str(project.path),
                "modified_at": project.modified_at or utc_now(),
                "clip_count": len(project.clips),
            },
        )
        self._write_registry(entries)

    def forget_recent(self, file_path: Path | str) -> None:
        target = Path(file_path).resolve()
        entries = [
            e for e in self._read_registry() if Path(e.get("file_path", "")).resolve() != target
        ]
        self._write_registry(entries)

    def create_project(self, name: str) -> Project:
        clean = safe_name(name)
        if not clean or clean == "Untitled":
            raise ProjectError("Enter a project name")
        workspace = unique_workspace(clean)
        workspace.mkdir(parents=True, exist_ok=True)
        for sub in ("media", "thumbnails", "exports", "cache"):
            (workspace / sub).mkdir(exist_ok=True)
        file_path = new_project_file(workspace, clean)
        if file_path.exists():
            raise ProjectError(f"A project already exists at {file_path}")
        project = Project(name=clean, file_path=str(file_path))
        repo = ProjectRepository(file_path)
        try:
            repo.save(project)
        finally:
            repo.close()
        self.touch_recent(project)
        log.info("created project %s at %s", clean, file_path)
        return project

    def open_project(self, file_path: Path | str) -> Project:
        path = Path(file_path)
        if path.is_dir():
            candidates = list(path.glob(f"*{PROJECT_EXTENSION}"))
            if not candidates:
                raise ProjectError(f"No {PROJECT_EXTENSION} file found in {path}")
            path = candidates[0]
        if path.suffix.lower() != PROJECT_EXTENSION:
            path = path.with_suffix(PROJECT_EXTENSION)
        if not path.exists():
            raise ProjectError(f"Project file not found: {path}")
        repo = ProjectRepository(path)
        try:
            project = repo.load()
        except Exception as exc:
            raise ProjectError(f"Project cannot be loaded: {exc}") from exc
        finally:
            repo.close()
        self.touch_recent(project)
        return project

    def save_project(self, project: Project) -> None:
        project.ensure_dirs()
        repo = ProjectRepository(project.path)
        try:
            repo.save(project)
        except Exception as exc:
            raise ProjectError(f"Project cannot be saved: {exc}") from exc
        finally:
            repo.close()
        self.touch_recent(project)

    def save_project_as(self, project: Project, target: Path | str) -> Project:
        target = Path(target)
        if target.suffix.lower() != PROJECT_EXTENSION:
            target = target.with_suffix(PROJECT_EXTENSION)
        target.parent.mkdir(parents=True, exist_ok=True)
        project.name = target.stem
        project.file_path = str(target)
        project.ensure_dirs()
        for item in project.media:
            item.rel_path = None
        repo = ProjectRepository(target)
        try:
            repo.save(project)
        except Exception as exc:
            raise ProjectError(f"Project cannot be saved: {exc}") from exc
        finally:
            repo.close()
        self.touch_recent(project)
        return project
