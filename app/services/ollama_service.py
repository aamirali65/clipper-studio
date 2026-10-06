from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator, Sequence

from app.utils.logging import get_logger

log = get_logger("ollama")

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    """Ollama server unreachable or returned an error."""


def base_url(url: str = "") -> str:
    return (url or DEFAULT_URL).strip().rstrip("/") or DEFAULT_URL


def ping(url: str = "", timeout: float = 2.0) -> bool:
    """True when an Ollama server answers at ``url``."""
    try:
        with urllib.request.urlopen(f"{base_url(url)}/api/tags", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


def list_models(url: str = "", timeout: float = 3.0) -> list[str]:
    """Model names from ``GET /api/tags`` (e.g. ``qwen2.5-coder:3b``)."""
    endpoint = f"{base_url(url)}/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise OllamaError(
            f"Cannot reach Ollama at {base_url(url)} ({exc}). "
            "Install it from ollama.com and run: ollama serve"
        ) from exc
    except ValueError as exc:
        raise OllamaError(f"Bad response from Ollama at {base_url(url)}") from exc
    names: list[str] = []
    for entry in data.get("models") or []:
        name = str(entry.get("name") or "").strip()
        if name:
            names.append(name)
    return sorted(names)


def chat_stream(
    url: str,
    model: str,
    messages: Sequence[dict],
    *,
    handle: dict | None = None,
    options: dict | None = None,
    timeout: float = 300.0,
) -> Iterator[str]:
    """Stream assistant tokens from ``POST /api/chat`` (NDJSON).

    ``handle`` receives ``{"response": ...}`` as soon as the socket is open so
    a worker thread can close it to interrupt a long generation.
    """
    payload: dict = {
        "model": model,
        "messages": [dict(m) for m in messages],
        "stream": True,
    }
    if options:
        payload["options"] = options
    request = urllib.request.Request(
        f"{base_url(url)}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except OSError:
            pass
        raise OllamaError(f"Ollama error {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OllamaError(
            f"Cannot reach Ollama at {base_url(url)} ({exc}). "
            "Install it from ollama.com and run: ollama serve"
        ) from exc
    if handle is not None:
        handle["response"] = response
    try:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if data.get("error"):
                raise OllamaError(str(data["error"]))
            content = (data.get("message") or {}).get("content") or ""
            if content:
                yield content
            if data.get("done"):
                break
    finally:
        try:
            response.close()
        except OSError:
            pass
