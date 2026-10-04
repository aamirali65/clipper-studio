# Clipper Studio

Professional Windows desktop video clipping application. Local-first, no paid APIs.

Phase 1 foundation: project system, media import (local + YouTube), real video
playback, timeline trimming, and real FFmpeg export.

## Requirements

- Windows 10/11
- Python 3.11+ (tested on 3.14)
- FFmpeg on PATH (`ffmpeg.exe`, `ffprobe.exe`)

## Installation

```powershell
cd D:\Projectss\clipping
python -m pip install -r requirements.txt
```

Dependencies: `PySide6`, `pydantic`, `yt-dlp` (all free/local; no API keys).

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
python tests\test_pipeline.py   # timecode, probe, project SQLite, FFmpeg export (3 aspect ratios)
python tests\test_youtube.py    # YouTube URL validation
python tests\test_gui.py        # full GUI flow: project -> import -> trim -> save -> reopen -> export
```

Tests expect `output\sample_video.mp4`; it is generated automatically by
`test_pipeline.py` only if missing - otherwise create it first:

```powershell
ffmpeg -y -f lavfi -i "testsrc2=duration=30:size=1280x720:rate=30" -f lavfi -i "sine=frequency=440:duration=30" -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -shortest output\sample_video.mp4
```

## Usage (Phase 1)

1. `File > New Project` (or dashboard **Create New Project**)
2. **Import video** for a local file, or paste a YouTube URL and press
   Enter (metadata preview) / **Download**
3. Double-click a media card to load it into the preview
4. Trim: drag the timeline handles, or press `I` / `O` at the playhead, or
   type exact timecodes in the Inspector
5. Pick an aspect ratio (16:9 / 9:16 / 1:1 / 4:5) - the preview shows the
   framing mask
6. **Export** - real FFmpeg trim + crop to a MP4 (H.264/AAC) with live progress

### Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| Left / Right | Seek -5s / +5s |
| Shift+Left / Shift+Right | Seek -1s / +1s |
| I / O | Set in / out point |
| Ctrl+N / Ctrl+O | New / open project |
| Ctrl+S / Ctrl+Shift+S | Save / save as |
| Ctrl+I | Import video |
| Ctrl+E | Export clip |
| Delete | Remove selected clip or media |
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
│   │   ├── timeline.py      # playhead, ruler, draggable in/out, zoom, seek
│   │   ├── inspector.py     # clip start/end/duration/aspect + validation
│   │   ├── project_panel.py # project / editor / export pages
│   │   ├── export_dialog.py # export options + progress + open folder
│   │   ├── project_dialog.py# new project dialog
│   │   ├── status_bar.py    # timecode, aspect, export button
│   │   └── theme.py         # dark stylesheet
│   ├── services/            # FFmpegService, VideoService, YouTubeService,
│   │                        # ExportService, ProjectService
│   ├── database/            # database.py (SQLite schema) + repositories.py (all SQL)
│   ├── models/              # Pydantic: Project, MediaItem, Clip, settings
│   ├── workers/             # QThread: probe, download, metadata, export
│   └── utils/               # paths, logging, ffmpeg discovery, timecode
├── tests/
├── projects/                # workspaces: <name>/media, thumbnails, exports, cache, <name>.clipper
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

## Known limitations (Phase 1)

- Preview uses Qt Multimedia (WMF backend); exotic codecs (e.g. AV1, some
  VP9) may not play even though FFmpeg can export them. Convert or re-encode
  such files first.
- Frame-exact stepping is not implemented; arrows seek 5s (1s with Shift).
- Single video track with in/out trimming. Multi-clip timeline editing,
  split/move/duplicate, and undo/redo belong to Phase 2.
- Captions and AI sidebar entries are intentionally disabled - no fake
  functionality is provided before Phase 4-5.
- Smart crop is center-crop; face/subject tracking is Phase 7.
- YouTube download uses yt-dlp defaults (no cookies, no API key). Private or
  region-locked videos will fail with the reported yt-dlp error.
- Paths are resolved from the repository root (or the frozen EXE folder);
  there is no installer yet (PyInstaller packaging is Phase 12).
