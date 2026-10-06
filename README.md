<div align="center">
  <img src="assets/logo.png" alt="Clipper Studio logo" width="128">
  <h1>Clipper Studio</h1>
  <p>Local-first Windows desktop video clipper. Multi-clip timeline, real FFmpeg export, no paid APIs.</p>
  <p>
    <img src="https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square">
    <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square">
    <img src="https://img.shields.io/badge/PySide6-6.x-41CD52?style=flat-square">
    <img src="https://img.shields.io/badge/FFmpeg-6%2B-0078D6?style=flat-square">
  </p>
</div>

Professional Windows desktop video clipping application. Local-first, no paid APIs.

Phase 1 foundation: project system, media import (local + YouTube), real video
playback, timeline trimming, and real FFmpeg export.

Phase 2 timeline editor: multi-clip timeline with split, move, duplicate,
delete, undo/redo, timeline-to-source mapping, and one-click export of every
clip in a project.

Phase 3 export queue + settings: background export queue with per-job
progress, cancel/clear controls, and a SETTINGS page for global defaults
(aspect, quality preset, output folder) persisted in `settings.json`.

Phase 4 captions: local whisper transcription (faster-whisper, no cloud), an
editable CAPTIONS page with per-clip segments, SRT export, and optional
burned-in subtitles for single and queued exports.

Phase 5 AI assistant: a local Ollama chat with project/clip/transcript
context, quick actions (summary, titles, hashtags) and streamed responses -
fully offline, no API keys.

## Requirements

- Windows 10/11
- Python 3.11+ (tested on 3.14)
- FFmpeg on PATH (`ffmpeg.exe`, `ffprobe.exe`)
- Optional: [Ollama](https://ollama.com) for the AI assistant page

## Installation

```powershell
cd D:\Projectss\clipping
python -m pip install -r requirements.txt
```

Dependencies: `PySide6`, `pydantic`, `yt-dlp`, `faster-whisper` (all free/local; no API keys).

## FFmpeg setup

Option A - winget:

```powershell
winget install Gyan.FFmpeg
```

Option B - manual:

1. Download `ffmpeg-release-essentials.zip` from https://www.gyan.dev/ffmpeg/builds/
2. Extract to e.g. `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to your user `PATH`
4. Open a new terminal and verify:

```powershell
ffmpeg -version
ffprobe -version
```

If FFmpeg is missing, Clipper Studio still starts and shows a warning in the
status bar; export and metadata probing will not work until it is installed.

## Ollama setup (optional - AI assistant)

The AI page talks to a local [Ollama](https://ollama.com) server:

```powershell
winget install Ollama.Ollama
ollama serve            # usually already running as a tray service
ollama pull qwen2.5:3b  # any chat model works
```

The server URL defaults to `http://127.0.0.1:11434` (changeable on the
SETTINGS page). The AI page lists installed models; without Ollama the page
still opens and reports that the server is unreachable - everything else in
the app keeps working.

## Run

```powershell
python main.py
```

Automated smoke test (opens the window, auto-closes):

```powershell
python main.py --smoke 4000
```

## Tests

```powershell
python tests\test_pipeline.py        # timecode, probe, project SQLite, FFmpeg export (3 aspect ratios)
python tests\test_youtube.py         # YouTube URL validation
python tests\test_gui.py             # full GUI flow: project -> import -> trim -> save -> reopen -> export
python tests\test_queue_settings.py  # export queue worker + settings persistence
python tests\test_captions.py        # SRT round-trip, caption DB, burn-in export, captions page
python tests\test_ai.py              # prompt builders, Ollama service, AI page, live chat (skips if no server)
```

Tests expect `output\sample_video.mp4`; it is generated automatically by
`test_pipeline.py` only if missing - otherwise create it first:

```powershell
ffmpeg -y -f lavfi -i "testsrc2=duration=30:size=1280x720:rate=30" -f lavfi -i "sine=frequency=440:duration=30" -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -shortest output\sample_video.mp4
```

## Usage (Phase 2)

1. `File > New Project` (or dashboard **Create New Project**)
2. **Import video** for a local file, or paste a YouTube URL and press
   Enter (metadata preview) / **Download**
3. Double-click a media card to load it into the preview
4. Trim: drag the timeline handles, or press `I` / `O` at the playhead, or
   type exact timecodes in the Inspector
5. Build a sequence: **Add clip** in the editor page, drag clips on the
   timeline to reorder, press `S` at the playhead to split, `Ctrl+D` to
   duplicate, `Delete` to remove
6. Undo / redo any edit with `Ctrl+Z` / `Ctrl+Shift+Z`
7. Pick an aspect ratio (16:9 / 9:16 / 1:1 / 4:5) - the preview shows the
   framing mask
8. **Export** one clip (`Ctrl+E`), or send it to the **queue** from the
   EXPORT page, or queue the whole project (`Ctrl+Shift+E`) - real FFmpeg
   trim + crop to MP4 (H.264/AAC) with live per-job progress on the QUEUE
   page while you keep editing
9. Set global defaults (new-project aspect, quality preset, export output
   folder) on the SETTINGS page

### Captions (Phase 4)

1. Select a clip, open the **CAPTIONS** page in the sidebar, pick a whisper
   model (tiny/base/small/medium) and language on the SETTINGS or CAPTIONS
   page, and press **Transcribe** - audio is extracted with FFmpeg and
   transcribed locally in a background thread
2. The first run downloads the selected model once (tiny ~75 MB, base
   ~145 MB) into `cache/whisper/`; later runs reuse it
3. Double-click any segment row to edit the text; edits save immediately
4. **Export SRT** writes a `.srt` file for the clip
5. Enable *Burn captions into exports* on SETTINGS (or tick the checkbox in
   the export dialog) to burn subtitles into single and queued exports

Each clip keeps its own in/out range (source mapping) and timeline position,
so what you see on the timeline is exactly what gets exported.

### AI assistant (Phase 5)

1. Open the **AI** page - it lists the models your local Ollama server has
   installed (press **Refresh** after changing the URL on SETTINGS)
2. Ask anything in the input box; answers stream in live and the assistant
   sees the project's clip list plus the selected clip's transcript
3. With a clip selected, use the **Summary / Titles / Hashtags** quick
   actions for one-click prompts grounded in that clip's captions
4. **Clear chat** resets the conversation (history is session-only)
5. Nothing leaves your machine - the app only talks to the Ollama URL in
   settings (default `http://127.0.0.1:11434`)

### Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| Left / Right | Seek -5s / +5s |
| Shift+Left / Shift+Right | Seek -1s / +1s |
| I / O | Set in / out point |
| S | Split selected clip at playhead |
| Ctrl+D | Duplicate selected clip |
| Delete | Remove selected clip or media |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| Ctrl+N / Ctrl+O | New / open project |
| Ctrl+S / Ctrl+Shift+S | Save / save as |
| Ctrl+I | Import video |
| Ctrl+E | Export current clip |
| Ctrl+Shift+E | Export all clips |
| Mouse wheel | Timeline zoom |

## Project structure

```
clipper-studio/
├── main.py                  # entry point, excepthook, --smoke
├── requirements.txt / pyproject.toml
├── app/
│   ├── ui/                  # widgets only (no shell commands, no SQL)
│   │   ├── main_window.py   # composition, actions, shortcuts, orchestration
│   │   ├── dashboard.py     # startup screen + recent projects
│   │   ├── sidebar.py       # MEDIA / PROJECT / EDITOR / CAPTIONS / AI / EXPORT nav
│   │   ├── media_panel.py   # import + YouTube download + media cards
│   │   ├── video_player.py  # QMediaPlayer preview + aspect framing overlay
│   │   ├── timeline.py      # multi-clip timeline: split/move/drag, zoom, undo support
│   │   ├── inspector.py     # clip start/end/duration/aspect + validation
│   │   ├── project_panel.py # project / editor / export pages
│   │   ├── queue_panel.py   # QUEUE page: background export jobs + progress
│   │   ├── settings_panel.py# SETTINGS page: global defaults
│   │   ├── captions_panel.py# CAPTIONS page: transcript segments + transcribe
│   │   ├── ai_panel.py      # AI page: local Ollama chat + quick actions
│   │   ├── export_dialog.py # export options + progress + open folder
│   │   ├── project_dialog.py# new project dialog
│   │   ├── status_bar.py    # timecode, aspect, export button
│   │   └── theme.py         # dark stylesheet
│   ├── services/            # FFmpegService, VideoService, YouTubeService,
│   │                        # ExportService, ExportQueueWorker, SettingsService,
│   │                        # ProjectService, CaptionService, OllamaService,
│   │                        # AiService (prompt builders)
│   ├── database/            # database.py (SQLite schema) + repositories.py (all SQL)
│   ├── models/              # Pydantic: Project, MediaItem, Clip, ExportJob,
│   │                        # CaptionSegment, AppSettings, settings
│   ├── workers/             # QThread: probe, download, metadata, export,
│   │                        # transcribe, ollama chat + model list
│   └── utils/               # paths, logging, ffmpeg discovery, timecode
├── tests/
├── projects/                # workspaces: <name>/media, thumbnails, exports, cache, <name>.clipper
├── cache/whisper/           # downloaded whisper models (first transcription run)
├── settings.json            # global defaults (created after first save)
├── logs/clipper.log
└── output/
```

Each project is a folder containing a SQLite file with a `.clipper` extension:

```
projects/MyPodcast/
├── MyPodcast.clipper   # SQLite: media, clips, settings
├── media/  thumbnails/  exports/  cache/
```

Paths inside the project are stored relative to the project folder, so a
workspace can be moved as long as external media files are relocated too.

## Known limitations

- Preview uses Qt Multimedia (WMF backend); exotic codecs (e.g. AV1, some
  VP9) may not play even though FFmpeg can export them. Convert or re-encode
  such files first.
- Frame-exact stepping is not implemented; arrows seek 5s (1s with Shift).
- Single video track with multi-clip arrangement; audio tracks are not
  editable. Captions run locally on CPU via faster-whisper: expect a short
  wait on the first run (model download) and slower-than-realtime
  transcription on weak hardware.
- The AI assistant needs a separately installed Ollama server with at least
  one pulled model; responses depend on your hardware and model size. Chat
  history is session-scoped (not saved to the project), and the transcript
  context sent to the model is capped at ~6000 characters.
- The export queue is session-scoped: queued jobs are not persisted across
  app restarts. Settings (aspect/preset/output folder) live in
  `settings.json` next to the app.
- Smart crop is center-crop; face/subject tracking is Phase 7.
- YouTube download uses yt-dlp defaults (no cookies, no API key). Private or
  region-locked videos will fail with the reported yt-dlp error.
- Paths are resolved from the repository root (or the frozen EXE folder);
  there is no installer yet (PyInstaller packaging is Phase 12).
