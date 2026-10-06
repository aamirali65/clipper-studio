from __future__ import annotations

import json
import re
from typing import Sequence

from app.models.caption import CaptionSegment
from app.models.highlight import SOURCE_AI, SOURCE_HEURISTIC, HighlightCandidate

PAUSE_GAP = 0.7  # seconds of silence that splits two utterances
SENTENCE_GAP = 0.05  # any boundary after .!? starts a new utterance
MAX_WINDOWS = 1500
MAX_UNITS_PER_WINDOW = 50

HOOK_WORDS = frozenset(
    {
        "how", "why", "secret", "never", "best", "mistake", "mistakes",
        "money", "free", "stop", "listen", "imagine", "proof", "truth",
        "hack", "trick", "warning", "everyone", "nobody", "million",
        "dollar", "important", "actually", "really", "insane", "crazy",
        "must", "change", "easy", "simple", "update", "nobody", "ever",
    }
)

_SENTENCE_END = tuple(".!?")

Utterance = tuple[float, float, str]  # start, end, text
Window = tuple[float, float, str]
AiWindow = tuple[float, float, str, str]  # start, end, title, reason


class _Scored:
    __slots__ = ("start", "end", "raw", "title", "reason", "source")

    def __init__(
        self,
        start: float,
        end: float,
        raw: int,
        title: str,
        reason: str,
        source: str = SOURCE_HEURISTIC,
    ):
        self.start = start
        self.end = end
        self.raw = raw
        self.title = title
        self.reason = reason
        self.source = source


def build_units(
    segments: Sequence[CaptionSegment], pause_gap: float = PAUSE_GAP
) -> list[Utterance]:
    """Merge transcript segments into utterance units.

    Splits on long pauses (or short pauses after sentence-ending punctuation)
    so windows align with natural speech boundaries.
    """
    units: list[Utterance] = []
    current: list[str] = []
    cur_start = 0.0
    cur_end = 0.0
    for segment in sorted(segments, key=lambda s: (s.start, s.end)):
        text = (segment.text or "").strip()
        if not text:
            continue
        gap = segment.start - cur_end if current else 0.0
        prev_ends_sentence = bool(current) and current[-1].rstrip().endswith(
            _SENTENCE_END
        )
        split = bool(current) and (
            gap >= pause_gap or (prev_ends_sentence and gap >= SENTENCE_GAP)
        )
        if split:
            units.append((cur_start, cur_end, " ".join(current)))
            current = []
        if not current:
            cur_start = segment.start
        current.append(text)
        cur_end = max(cur_end, segment.end)
    if current:
        units.append((cur_start, cur_end, " ".join(current)))
    return units


def build_windows(
    units: Sequence[Utterance], min_len: float, max_len: float
) -> list[Window]:
    """Forward-growing windows whose duration lands in [min_len, max_len]."""
    if not units or min_len <= 0 or max_len < min_len:
        return []
    windows: list[Window] = []
    seen: set[tuple[float, float]] = set()
    total = len(units)
    for i in range(total):
        start = units[i][0]
        parts: list[str] = []
        limit = min(i + MAX_UNITS_PER_WINDOW, total)
        for j in range(i, limit):
            parts.append(units[j][2])
            end = units[j][1]
            duration = end - start
            if duration > max_len:
                break
            if duration >= min_len:
                key = (round(start, 2), round(end, 2))
                if key not in seen:
                    seen.add(key)
                    windows.append((start, end, " ".join(parts)))
            if len(windows) >= MAX_WINDOWS:
                return windows
    return windows


def make_title(text: str, max_len: int = 48) -> str:
    first = re.split(r"[.!?;:\n]", text.strip(), maxsplit=1)[0].strip()
    first = first.strip("\"'()[]{}")
    if not first:
        return "Highlight"
    if len(first) > max_len:
        trimmed = first[:max_len].rsplit(" ", 1)[0].rstrip(",;:- ")
        first = trimmed or first[:max_len]
    return first


def score_window(start: float, end: float, units: Sequence[Utterance]) -> _Scored | None:
    """Heuristic highlight score for [start, end) over overlapping utterances."""
    duration = end - start
    if duration <= 0:
        return None
    overlapping = [
        unit
        for unit in units
        if min(unit[1], end) - max(unit[0], start) > 0
    ]
    if not overlapping:
        return None
    text = " ".join(unit[2] for unit in overlapping).strip()
    if not text:
        return None
    words = text.split()
    words_per_second = len(words) / duration
    coverage = sum(
        min(unit[1], end) - max(unit[0], start) for unit in overlapping
    ) / duration
    coverage = max(0.0, min(1.0, coverage))
    plain = [w.strip(".,!?;:\"'()[]") for w in text.lower().split()]
    head = plain[:8]
    digits = sum(1 for w in plain if any(ch.isdigit() for ch in w))

    features: list[tuple[str, int]] = []
    if any(word in HOOK_WORDS for word in head):
        features.append(("hook opening", 26))
    questions = text.count("?")
    if questions:
        features.append(("questions", min(questions, 3) * 9))
    exclamations = text.count("!")
    if exclamations:
        features.append(("energy", min(exclamations, 3) * 4))
    if digits:
        features.append(("numbers", min(digits, 4) * 5))
    if 2.2 <= words_per_second <= 4.8:
        features.append(("dense speech", 16))
    elif words_per_second < 1.3:
        features.append(("slow speech", -10))
    elif words_per_second > 6.5:
        features.append(("too fast", -6))
    features.append(("good coverage", round(coverage * 16)))
    if text.rstrip().endswith(_SENTENCE_END):
        features.append(("clean ending", 6))
    if coverage < 0.45:
        features.append(("dead air", -20))

    raw = 40 + sum(points for _label, points in features)
    positives = sorted(
        ((label, points) for label, points in features if points > 0),
        key=lambda item: item[1],
        reverse=True,
    )
    reason = ", ".join(label for label, _points in positives[:2]) or "balanced"
    return _Scored(start, end, max(0, raw), make_title(text), reason)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def nms_pick(pool: Sequence[_Scored], count: int, min_gap: float = 1.0) -> list[_Scored]:
    ordered = sorted(pool, key=lambda item: item.raw, reverse=True)
    picked: list[_Scored] = []
    for item in ordered:
        if len(picked) >= count:
            break
        if all(
            _overlap((item.start, item.end), (other.start, other.end)) < min_gap
            for other in picked
        ):
            picked.append(item)
    return picked


def analyze(
    segments: Sequence[CaptionSegment],
    *,
    media_id: int,
    media_duration: float = 0.0,
    min_len: float = 15.0,
    max_len: float = 45.0,
    count: int = 5,
    ai_windows: Sequence[AiWindow] = (),
) -> list[HighlightCandidate]:
    """Rank highlight windows from a transcript (heuristic + optional AI).

    ``ai_windows`` are model-proposed ``(start, end, title, reason)`` ranges;
    they join the heuristic pool with a score bonus and share one
    non-max-suppression pass so results never overlap.
    """
    units = build_units(segments)
    if not units:
        return []
    min_len = max(1.0, float(min_len))
    max_len = max(min_len, float(max_len))
    if media_duration > 0:
        max_len = min(max_len, media_duration)

    pool: list[_Scored] = []
    for window in build_windows(units, min_len, max_len):
        scored = score_window(window[0], window[1], units)
        if scored is not None:
            pool.append(scored)

    if not pool:
        # Media shorter than min_len: offer the whole speech span instead.
        start = units[0][0]
        end = units[-1][1]
        if end - start <= max_len:
            scored = score_window(start, end, units)
            if scored is not None:
                pool.append(scored)

    for window in ai_windows:
        start, end, title, reason = window
        if media_duration > 0:
            start = min(max(0.0, start), max(0.0, media_duration - 0.5))
            end = min(end, media_duration)
        if end - start <= 0:
            continue
        if end - start < min_len and media_duration > 0:
            end = min(start + min_len, media_duration)
        if end - start > max_len:
            end = start + max_len
        if end - start <= 0:
            continue
        scored = score_window(start, end, units)
        raw = (scored.raw if scored is not None else 40) + 25
        pool.append(
            _Scored(
                start,
                end,
                raw,
                (title or (scored.title if scored else "Highlight"))[:80],
                (reason or (scored.reason if scored else "AI pick"))[:120],
                source=SOURCE_AI,
            )
        )

    if not pool:
        return []
    if count < 1:
        count = 1
    picked = nms_pick(pool, count)
    if not picked:
        return []
    raws = [item.raw for item in picked]
    low, high = min(raws), max(raws)
    candidates: list[HighlightCandidate] = []
    for order, item in enumerate(picked):
        if high == low:
            score = 80  # no ranking information in an uniform batch
        else:
            score = 30 + int(round((item.raw - low) / (high - low) * 70))
        end = item.end
        if media_duration > 0:
            end = min(end, media_duration)
        candidates.append(
            HighlightCandidate(
                media_id=media_id,
                start=round(item.start, 3),
                end=round(end, 3),
                score=score,
                title=item.title,
                reason=item.reason,
                source=item.source,
                order=order,
            )
        )
    return candidates


def ai_rank_prompt(
    segments: Sequence[CaptionSegment],
    *,
    count: int,
    min_len: float,
    max_len: float,
    duration: float = 0.0,
    char_cap: int = 8000,
) -> str:
    lines: list[str] = []
    used = 0
    for segment in sorted(segments, key=lambda s: s.start):
        text = (segment.text or "").strip()
        if not text:
            continue
        line = f"[{segment.start:.1f}-{segment.end:.1f}] {text}"
        if used + len(line) > char_cap:
            break
        lines.append(line)
        used += len(line)
    duration_text = f"{duration:.0f}" if duration > 0 else "the video length"
    return (
        "You are picking the best short-form clip moments from a video "
        "transcript. Return ONLY a JSON array (no markdown fences, no "
        f"commentary) of exactly {count} objects like:\n"
        '[{"start": 12.5, "end": 47.0, "title": "short title", '
        '"reason": "why it hooks"}]\n'
        "Rules:\n"
        f"- start/end are seconds between 0 and {duration_text}\n"
        f"- each clip must last {min_len:.0f}-{max_len:.0f} seconds\n"
        "- prefer self-contained hooks, stories or punchlines; no overlaps\n"
        "- title <= 6 words, reason <= 12 words\n\n"
        "Transcript:\n" + "\n".join(lines)
    )


def parse_ai_windows(
    reply: str,
    *,
    duration: float = 0.0,
    min_len: float = 15.0,
    max_len: float = 45.0,
) -> list[AiWindow]:
    """Tolerantly extract ``(start, end, title, reason)`` from an LLM reply."""
    text = (reply or "").strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start_bracket = text.find("[")
    end_bracket = text.rfind("]")
    if start_bracket < 0 or end_bracket <= start_bracket:
        return []
    try:
        data = json.loads(text[start_bracket : end_bracket + 1])
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    windows: list[AiWindow] = []
    for item in data[:25]:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if duration > 0:
            start = min(max(0.0, start), max(0.0, duration - 0.5))
        if end <= start:
            continue
        if duration > 0:
            end = min(end, duration)
        if end - start > max_len:
            end = start + max_len
        if duration > 0 and end - start < min_len:
            end = min(start + min_len, duration)
        if end - start <= 0:
            continue
        title = str(item.get("title") or "").strip()
        reason = str(item.get("reason") or "").strip()
        windows.append((start, end, title, reason))
    return windows
