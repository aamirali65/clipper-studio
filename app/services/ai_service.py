from __future__ import annotations

from typing import Sequence

from app.models.caption import CaptionSegment
from app.models.clip import Clip
from app.models.project import Project
from app.utils.timecode import format_timecode

MAX_CONTEXT_CHARS = 6000
MAX_HISTORY = 12

SYSTEM_PROMPT = (
    "You are Clipper Studio's built-in assistant inside a local video "
    "clipping app for short-form content. You help users plan clips, "
    "summarize transcripts, write titles, descriptions and hashtags, and "
    "improve their edits. Be concise and practical. When asked for lists, "
    "reply with a short numbered list only. Use the project context below; "
    "never invent transcript lines that are not provided."
)

ACTIONS: dict[str, tuple[str, str]] = {
    # action id -> (chat bubble label, instruction sent to the model)
    "summary": (
        "Summarize this transcript",
        "Summarize the transcript in 3-5 short bullet points.",
    ),
    "titles": (
        "Suggest clip titles",
        "Suggest 5 catchy, specific short titles for this clip. "
        "Numbered list only, no commentary.",
    ),
    "hashtags": (
        "Suggest hashtags",
        "Suggest 10 relevant hashtags for this clip. One line, "
        "space-separated, each starting with #.",
    ),
}


def clip_context(
    clip: Clip,
    media_name: str = "",
    captions: Sequence[CaptionSegment] = (),
) -> str:
    lines = [f"Selected clip: {clip.name}"]
    if media_name:
        lines.append(f"Source media: {media_name}")
    lines.append(
        f"Range: {format_timecode(clip.start)} -> {format_timecode(clip.end)}"
        f" ({clip.duration:.2f}s), aspect {clip.aspect}"
    )
    transcript = " ".join(seg.text for seg in captions).strip()
    if transcript:
        if len(transcript) > MAX_CONTEXT_CHARS:
            transcript = transcript[:MAX_CONTEXT_CHARS] + " ..."
        lines.append("Transcript:")
        lines.append(transcript)
    else:
        lines.append("Transcript: (none yet - transcribe on the CAPTIONS page)")
    return "\n".join(lines)


def project_context(
    project: Project | None,
    clip: Clip | None = None,
    media_name: str = "",
    captions: Sequence[CaptionSegment] = (),
) -> str:
    if project is None:
        return "No project is open."
    clips = sorted(project.clips, key=lambda c: (c.timeline_start, c.order))
    lines = [f"Project: {project.name}", f"Clips ({len(clips)}):"]
    for existing in clips[:40]:
        marker = " (selected)" if clip is not None and existing.id == clip.id else ""
        lines.append(
            f"- {existing.name}: {format_timecode(existing.start)} -> "
            f"{format_timecode(existing.end)}, {existing.aspect}{marker}"
        )
    if clip is not None:
        lines.append(clip_context(clip, media_name, captions))
    return "\n".join(lines)


def build_messages(
    context: str,
    history: Sequence[dict],
    user_text: str,
) -> list[dict]:
    """System + capped chat history + the new user message."""
    messages: list[dict] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{context}"}
    ]
    valid = [
        {"role": str(item["role"]), "content": str(item["content"])}
        for item in history
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]
    messages.extend(valid[-MAX_HISTORY:])
    messages.append({"role": "user", "content": user_text})
    return messages


def action_prompt(action: str, context: str) -> tuple[str, str] | None:
    """(chat bubble label, full user message) for a quick action."""
    entry = ACTIONS.get(action)
    if entry is None:
        return None
    label, instruction = entry
    return label, f"{context}\n\nTask: {instruction}"
