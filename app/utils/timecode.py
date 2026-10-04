from __future__ import annotations


def format_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


def format_clock(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timecode(text: str) -> float:
    raw = text.strip().replace(",", ".")
    if not raw:
        raise ValueError("empty timecode")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(f"invalid timecode: {text}")
    seconds = 0.0
    for part in parts:
        if part == "" or not part.replace(".", "", 1).isdigit():
            raise ValueError(f"invalid timecode: {text}")
        seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError("timecode must be positive")
    return seconds
