from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.models.clip import Clip, ExportSettings
from app.models.project import Project
from app.models.queue import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_ERROR,
    JOB_QUEUED,
    JOB_RUNNING,
    ExportJob,
)
from app.services.export_service import ExportError, ExportRequest, ExportService
from app.utils.logging import get_logger
from app.utils.paths import safe_name

log = get_logger("export_queue")


class ExportQueueWorker(QThread):
    """Processes export jobs sequentially in the background.

    The worker thread lives for the whole application session and sleeps on
    a condition variable while the queue is empty, so enqueueing can never
    race with thread startup.
    """

    jobAdded = Signal(object)      # ExportJob
    jobUpdated = Signal(object)    # ExportJob
    jobsCleared = Signal()
    queueEmpty = Signal(int, int)  # done_count, error_count

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: list[ExportJob] = []
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stopping = False
        self._empty_emitted = True
        self._cycle_done = 0
        self._cycle_errors = 0
        self._next_id = 1
        self._current_id: int | None = None
        self._cancel_event: threading.Event | None = None
        self._service = ExportService()

    # ------------------------------------------------------------ enqueue
    def enqueue_clip(
        self,
        project: Project,
        clip: Clip,
        output_dir: str = "",
        settings: ExportSettings | None = None,
        autostart: bool = True,
    ) -> ExportJob:
        media = project.media_by_id(clip.media_id)
        settings = settings or project.export_settings

        source = ""
        if media is not None:
            resolved = media.resolve_path(project.directory)
            if resolved.exists():
                source = str(resolved)
            elif Path(media.source_path).exists():
                source = media.source_path

        base_dir = Path(output_dir) if output_dir else project.exports_dir
        name = " - ".join(
            part
            for part in (
                safe_name(project.name),
                safe_name(media.name) if media else "",
                safe_name(clip.name),
            )
            if part
        )
        output = self._unique_output(base_dir / f"{name}.mp4")

        with self._lock:
            job = ExportJob(
                id=self._next_id,
                clip_name=clip.name,
                media_name=media.name if media else "",
                aspect=clip.aspect,
                source=source,
                output=str(output),
                start=clip.start,
                end=clip.end,
                preset=settings.preset,
                crf=settings.crf,
                pixel_format=settings.pixel_format,
                project_name=project.name,
                project_path=str(project.path),
            )
            self._next_id += 1
            if not source:
                job.status = JOB_ERROR
                job.error = "Source file not found" if media else "Media missing from project"
            else:
                if self._empty_emitted:
                    self._cycle_done = 0
                    self._cycle_errors = 0
                self._jobs.append(job)
                self._empty_emitted = False
            self._cond.notify_all()
        self.jobAdded.emit(job)
        if job.status == JOB_ERROR:
            log.warning("enqueue failed for %s: %s", clip.name, job.error)
        elif autostart:
            with self._lock:
                should_start = not self._stopping and not self.isRunning()
            if should_start:
                self.start()
        return job

    def _unique_output(self, output: Path) -> Path:
        reserved = {job.output for job in self._jobs}
        candidate = output
        counter = 2
        while str(candidate) in reserved or candidate.exists():
            candidate = output.with_name(
                f"{output.stem} ({counter}){output.suffix}"
            )
            counter += 1
        return candidate

    # ------------------------------------------------------------ control
    def cancel_job(self, job_id: int) -> None:
        with self._lock:
            job = self._job_unlocked(job_id)
            if job is None:
                return
            if job.status == JOB_QUEUED:
                job.status = JOB_CANCELLED
                job.detail = "cancelled"
            elif job.status == JOB_RUNNING and self._current_id == job_id:
                if self._cancel_event is not None:
                    self._cancel_event.set()
        self.jobUpdated.emit(job)

    def cancel_all(self) -> None:
        with self._lock:
            current = self._current_id
            cancel_event = self._cancel_event
            for job in self._jobs:
                if job.status == JOB_QUEUED:
                    job.status = JOB_CANCELLED
                    job.detail = "cancelled"
            updated = [j for j in self._jobs if j.status == JOB_CANCELLED]
            if cancel_event is not None and current is not None:
                cancel_event.set()
        for job in updated:
            self.jobUpdated.emit(job)

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if not j.is_finished]
            removed = before - len(self._jobs)
        if removed:
            self.jobsCleared.emit()
        return removed

    def jobs_snapshot(self) -> list[ExportJob]:
        with self._lock:
            return [job.model_copy() for job in self._jobs]

    def counts(self) -> dict[str, int]:
        with self._lock:
            tallies = {
                JOB_QUEUED: 0,
                JOB_RUNNING: 0,
                JOB_DONE: 0,
                JOB_ERROR: 0,
                JOB_CANCELLED: 0,
            }
            for job in self._jobs:
                tallies[job.status] = tallies.get(job.status, 0) + 1
            return tallies

    def shutdown(self, timeout_ms: int = 8000) -> None:
        self.cancel_all()
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self.isRunning():
            self.wait(timeout_ms)

    # ------------------------------------------------------------- worker
    def run(self) -> None:
        while True:
            with self._cond:
                while not self._stopping and not self._has_queued_unlocked():
                    if not self._empty_emitted:
                        self._emit_empty_unlocked()
                    self._cond.wait(0.5)
                if self._stopping:
                    break
                job = self._next_queued_unlocked()
                if job is None:
                    continue
                job.status = JOB_RUNNING
                job.progress = 0.0
                job.detail = "starting"
                self._current_id = job.id
                self._cancel_event = threading.Event()
                cancel_event = self._cancel_event
                snapshot = job.model_copy()

            self.jobUpdated.emit(snapshot)
            self._run_job(job, cancel_event)

            with self._lock:
                self._current_id = None
                self._cancel_event = None

    def _run_job(self, job: ExportJob, cancel_event: threading.Event) -> None:
        request = ExportRequest(
            source=Path(job.source),
            output=Path(job.output),
            start=job.start,
            end=job.end,
            aspect=job.aspect,
            settings=ExportSettings(
                preset=job.preset, crf=job.crf, pixel_format=job.pixel_format
            ),
        )
        last_emit = -1.0

        def on_progress(percent: float, detail: str) -> None:
            nonlocal last_emit
            progress = max(0.0, min(1.0, percent / 100.0))
            if progress - last_emit < 0.01 and detail == job.detail:
                return
            last_emit = progress
            with self._lock:
                job.progress = progress
                job.detail = detail
                snapshot = job.model_copy()
            self.jobUpdated.emit(snapshot)

        try:
            self._service.export(
                request, progress_cb=on_progress, cancel_event=cancel_event
            )
        except ExportError as exc:
            cancelled = cancel_event.is_set()
            with self._lock:
                job.status = JOB_CANCELLED if cancelled else JOB_ERROR
                job.error = "" if cancelled else str(exc)
                job.detail = "cancelled" if cancelled else str(exc)
                if not cancelled:
                    self._cycle_errors += 1
                snapshot = job.model_copy()
            log.info(
                "queue job %s %s: %s",
                job.id,
                job.status,
                job.clip_name,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("queue job %s crashed", job.id)
            with self._lock:
                job.status = JOB_ERROR
                job.error = f"Export failed: {exc}"
                job.detail = job.error
                self._cycle_errors += 1
                snapshot = job.model_copy()
        else:
            with self._lock:
                job.status = JOB_DONE
                job.progress = 1.0
                job.detail = "done"
                self._cycle_done += 1
                snapshot = job.model_copy()
            log.info("queue job %s done: %s", job.id, job.output)
        self.jobUpdated.emit(snapshot)

    # ----------------------------------------------------------- helpers
    def _has_queued_unlocked(self) -> bool:
        return any(job.status == JOB_QUEUED for job in self._jobs)

    def _next_queued_unlocked(self) -> ExportJob | None:
        for job in self._jobs:
            if job.status == JOB_QUEUED:
                return job
        return None

    def _job_unlocked(self, job_id: int) -> ExportJob | None:
        for job in self._jobs:
            if job.id == job_id:
                return job
        return None

    def _emit_empty_unlocked(self) -> None:
        done = self._cycle_done
        errors = self._cycle_errors
        self._empty_emitted = True
        self.queueEmpty.emit(done, errors)
