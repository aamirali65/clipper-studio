from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.youtube_service import (
    InvalidYouTubeURL,
    YouTubeService,
)

is_valid_youtube_url = YouTubeService.is_valid_url


def test_url_validation():
    valid = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "http://youtube.com/watch?v=abc123XYZ_0",
        "https://m.youtube.com/watch?v=abc",
        "https://music.youtube.com/watch?v=abc",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/live/abc123",
        "www.youtube.com/watch?v=abc",
        "https://www.youtube.com/embed/abc",
    ]
    for url in valid:
        assert is_valid_youtube_url(url), f"should be valid: {url}"

    invalid = [
        "",
        "   ",
        "https://example.com/watch?v=abc",
        "https://vimeo.com/12345",
        "https://www.youtube.com/",
        "not a url",
        "ftp://youtube.com/watch?v=abc",
        "https://evil-youtube.com/watch?v=abc",
        "https://youtube.com.evil.example/watch?v=abc",
    ]
    for url in invalid:
        assert not is_valid_youtube_url(url), f"should be invalid: {url!r}"

    service = YouTubeService()
    assert service.validate("https://youtu.be/abc") == "https://youtu.be/abc"
    try:
        service.validate("https://example.com/x")
    except InvalidYouTubeURL:
        pass
    else:
        raise AssertionError("validate should raise for bad URL")
    print(f"PASS url validation ({len(valid)} valid, {len(invalid)} invalid)")


if __name__ == "__main__":
    test_url_validation()
    print("ALL YOUTUBE TESTS PASSED")
