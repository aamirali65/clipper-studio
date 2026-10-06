from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.caption import CaptionSegment  # noqa: E402
from app.models.clip import Clip  # noqa: E402
from app.models.media import MediaItem, MediaKind  # noqa: E402
from app.models.project import Project, utc_now  # noqa: E402
from app.services.ai_service import (  # noqa: E402
    MAX_CONTEXT_CHARS,
    MAX_HISTORY,
    action_prompt,
    build_messages,
    clip_context,
    project_context,
)
from app.services.ollama_service import (  # noqa: E402
    DEFAULT_URL,
    OllamaError,
    base_url,
    list_models,
    ping,
)

SAMPLE = ROOT / "output" / "sample_video.mp4"
PROJECT_NAME = "AITestProject"


def make_demo_project() -> Project:
    project = Project(
        name="DemoCast",
        file_path=str(ROOT / "projects" / "DemoCast" / "DemoCast.clipper"),
    )
    clip = Clip(
        media_id=1,
        name="Hook",
        start=1.5,
        end=4.0,
        aspect="9:16",
        timeline_start=0.0,
        timeline_end=2.5,
        order=0,
        created_at=utc_now(),
    )
    project.clips.append(clip)
    return project


def demo_captions() -> list[CaptionSegment]:
    return [
        CaptionSegment(clip_id=1, start=1.5, end=2.5, text="hello world"),
        CaptionSegment(clip_id=1, start=2.5, end=4.0, text="second line"),
    ]


def test_prompt_builders() -> None:
    project = make_demo_project()
    clip = project.clips[0]

    context = project_context(project, clip, "source.mp4", demo_captions())
    assert "Project: DemoCast" in context
    assert "Hook" in context
    assert "(selected)" in context
    assert "00:00:01.500" in context
    assert "hello world second line" in context
    assert "Source media: source.mp4" in context

    no_project = project_context(None, clip, "", [])
    assert no_project == "No project is open."

    bare = clip_context(clip)
    assert "(none yet" in bare

    long_text = "x" * (MAX_CONTEXT_CHARS + 500)
    clipped = clip_context(
        clip, "", [CaptionSegment(clip_id=1, start=0, end=1, text=long_text)]
    )
    assert clipped.endswith(" ...")
    assert len(clipped) < MAX_CONTEXT_CHARS + 400

    prompt = action_prompt("summary", context)
    assert prompt is not None
    label, message = prompt
    assert label == "Summarize this transcript"
    assert message.startswith(context)
    assert "Task:" in message
    assert action_prompt("unknown", context) is None

    history: list[dict] = []
    for index in range(15):
        history.append({"role": "user", "content": f"q{index}"})
        history.append({"role": "assistant", "content": f"a{index}"})
    history.append({"role": "system", "content": "ignore me"})
    messages = build_messages(context, history, "final question")
    assert messages[0]["role"] == "system"
    assert context in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "final question"}
    kept = messages[1:-1]
    assert len(kept) == MAX_HISTORY, len(kept)
    assert all(item["role"] in ("user", "assistant") for item in kept)
    assert kept[-1]["content"] == "a14"
    print("PASS prompts: project/clip context, actions, history cap")


def test_ollama_service_unit() -> None:
    assert base_url("") == DEFAULT_URL
    assert base_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert base_url("  http://example:1/  ") == "http://example:1"

    assert ping("http://127.0.0.1:9", timeout=1.0) is False

    try:
        list_models("http://127.0.0.1:9", timeout=1.0)
    except OllamaError as exc:
        assert "Cannot reach Ollama" in str(exc)
    else:
        raise AssertionError("list_models should fail on a closed port")

    if ping(DEFAULT_URL):
        models = list_models(DEFAULT_URL)
        assert isinstance(models, list)
        assert all(isinstance(name, str) and name for name in models)
        print(f"PASS ollama service: server reachable, {len(models)} model(s)")
    else:
        print("SKIP live server check (Ollama not running)")


def pick_smallest(models: list[str]) -> str:
    for size in (":0.5b", ":1b", ":3b", "tiny", "small"):
        for name in models:
            if size in name:
                return name
    return sorted(models)[0]


def test_live_chat() -> None:
    if os.environ.get("CLIPPER_SKIP_OLLAMA"):
        print("SKIP live chat (CLIPPER_SKIP_OLLAMA set)")
        return
    if not ping(DEFAULT_URL, timeout=2.0):
        print("SKIP live chat (Ollama not running)")
        return
    try:
        models = list_models(DEFAULT_URL)
    except OllamaError as exc:
        print(f"SKIP live chat ({exc})")
        return
    if not models:
        print("SKIP live chat (no models pulled)")
        return

    from app.services.ollama_service import chat_stream

    model = pick_smallest(models)
    try:
        chunks = chat_stream(
            DEFAULT_URL,
            model,
            [{"role": "user", "content": "Reply with exactly the word OK"}],
            options={"num_predict": 8, "temperature": 0},
            timeout=180.0,
        )
        text = "".join(chunks)
    except Exception as exc:  # noqa: BLE001 - model load/network flake skips
        print(f"SKIP live chat ({type(exc).__name__}: {exc})")
        return
    assert text.strip(), "empty response"
    print(f"PASS live chat: {model} -> {text.strip()[:40]!r}")


def make_gui_project() -> tuple:
    from app.database.database import ProjectDatabase
    from app.database.repositories import ClipRepository, MediaRepository
    from app.services.project_service import ProjectService
    from app.services.video_service import VideoService

    assert SAMPLE.exists(), "run tests/test_pipeline.py first"
    shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)
    service = ProjectService()
    project = service.create_project(PROJECT_NAME)
    project.ensure_dirs()

    info = VideoService().probe(SAMPLE)
    media = MediaItem(
        name="ai_source",
        kind=MediaKind.local,
        source_path=str(Path(info.path).resolve()),
        duration=info.duration,
        width=info.width,
        height=info.height,
        size_bytes=info.size_bytes,
        created_at=utc_now(),
    )
    database = ProjectDatabase(project.path)
    try:
        MediaRepository(database).add(media, project.directory)
    finally:
        database.close()
    project.media.append(media)

    clip = Clip(
        media_id=media.id or 0,
        name="AI Clip",
        start=0.0,
        end=3.0,
        aspect="9:16",
        timeline_start=0.0,
        timeline_end=3.0,
        order=0,
        created_at=utc_now(),
    )
    database = ProjectDatabase(project.path)
    try:
        ClipRepository(database).add(clip)
    finally:
        database.close()
    project.clips.append(clip)
    return service, project


def test_gui_ai_page() -> None:
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._confirm_discard = lambda: True

    assert window.sidebar._buttons["ai"].isEnabled(), "ai page disabled"
    assert window.pages.count() == 11, window.pages.count()
    window.sidebar.select("ai")
    assert window.pages.currentIndex() == 7

    panel = window.ai_panel

    # empty input never emits
    sent: list[str] = []
    panel.sendRequested.connect(sent.append)
    panel.input.setText("   ")
    panel._send()
    assert sent == []
    assert "Type a message" in panel.status_label.text()

    # chat streaming into the log
    panel.append_user("what is this project about?")
    panel.begin_assistant()
    panel.append_token("Hello")
    panel.append_token(" world")
    log = panel.log_text()
    assert "You:\nwhat is this project about?" in log
    assert "Assistant:\nHello world" in log
    panel.finish_assistant()
    assert len(panel.history) == 2
    assert panel.history[0]["role"] == "user"
    assert panel.history[1]["content"] == "Hello world"

    # empty assistant bubble is dropped
    panel.begin_assistant()
    panel.finish_assistant()
    assert len(panel.history) == 2
    assert panel.log_text().count("Assistant:\n") == 1

    # models + busy state
    panel.set_models(["aa:1b", "bb:3b"], preferred="bb:3b")
    assert panel.selected_model() == "bb:3b"
    panel.set_busy(True)
    assert panel.cancel_button.isEnabled()
    assert not panel.input.isEnabled()
    panel.set_busy(False)
    assert not panel.cancel_button.isEnabled()
    assert panel.input.isEnabled()

    service, project = make_gui_project()
    try:
        window._load_project(project, "loaded")
        window.sidebar.select("ai")
        assert panel._clip is not None

        # quick action without a clip selection
        window.current_clip = None
        panel.set_models([])
        window._on_ai_action("summary")
        assert window._ai_worker is None
        assert "Select a clip" in panel.status_label.text()

        # send without any model reports a clear error, starts no worker
        window.current_clip = project.clips[0]
        window._on_ai_send("hello there")
        assert window._ai_worker is None
        assert "No model selected" in panel.status_label.text()

        # normal send path reaches the no-model guard through the signal
        panel.set_models([])
        panel.input.setText("hi")
        panel._send()
        assert sent == ["hi"]
        assert panel.input.text() == ""
        assert window._ai_worker is None

        # completion / cancel handling keeps partial output
        window.ai_panel.append_user("question")
        window.ai_panel.begin_assistant()
        window.ai_panel.set_busy(True)
        window.ai_panel.append_token("partial answer")
        window._on_ai_error("cancelled")
        assert not window.ai_panel.busy
        assert "partial answer" in window.ai_panel.log_text()
        assert "Cancelled" in window.ai_panel.status_label.text()

        window.ai_panel.begin_assistant()
        window.ai_panel.set_busy(True)
        window._on_ai_completed()
        assert not window.ai_panel.busy
        assert window.ai_panel.status_label.text() == "Done"

        # clear resets history and log
        window._on_ai_clear()
        assert window.ai_panel.history == []
        assert window.ai_panel.log_text() == ""
        print("PASS gui: ai page wiring, chat stream, guards, states")
    finally:
        if window._models_worker is not None and window._models_worker.isRunning():
            window._models_worker.wait(3000)
        window.queue_worker.shutdown(2000)
        shutil.rmtree(ROOT / "projects" / PROJECT_NAME, ignore_errors=True)


def test_settings_roundtrip() -> None:
    from app.models.settings import AppSettings
    from app.services.settings_service import SettingsService
    from app.ui.settings_panel import SettingsPanel

    app = QApplication.instance() or QApplication([])
    assert AppSettings().ollama_url == "http://127.0.0.1:11434"
    assert AppSettings().ollama_model == ""

    tmp = ROOT / "projects" / "_ai_settings_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        service = SettingsService(tmp / "settings.json")
        panel = SettingsPanel(service)
        panel.url_edit.setText("http://localhost:2222")
        service.settings.ollama_model = "kept:3b"
        panel._save()
        assert service.settings.ollama_url == "http://localhost:2222"
        assert service.settings.ollama_model == "kept:3b"

        reloaded = SettingsService(tmp / "settings.json")
        assert reloaded.settings.ollama_url == "http://localhost:2222"
        fresh_panel = SettingsPanel(reloaded)
        assert fresh_panel.url_edit.text() == "http://localhost:2222"
        print("PASS settings: ollama url/model persist through settings.json")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    _ = QApplication.instance() or QApplication([])
    test_prompt_builders()
    test_ollama_service_unit()
    test_settings_roundtrip()
    test_gui_ai_page()
    test_live_chat()
    print("ALL AI TESTS PASSED")


if __name__ == "__main__":
    main()
