"""Configuration validation and translation to Hermes Gadget Linux config."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

_ALLOWED = {"server", "name", "token", "audio", "minitoo"}
_MINITOO_ALLOWED = {
    "address", "channel", "max_fps", "packet_delay_ms", "request_timeout_ms",
    "reconnect_delay_ms", "zstd_level", "zstd_window_log",
}


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - _ALLOWED:
        raise ValueError("unknown config field; expected server, name, token, audio or minitoo")
    if not isinstance(raw.get("server"), str):
        raise ValueError("server is required")
    url = urlsplit(raw["server"])
    if url.scheme not in {"ws", "wss"} or not url.hostname:
        raise ValueError("server must be a ws:// or wss:// URL")
    mini = raw.get("minitoo")
    if not isinstance(mini, dict) or set(mini) - _MINITOO_ALLOWED:
        raise ValueError("minitoo must be an object with known MiniToo options")
    if not isinstance(mini.get("address"), str) or ":" not in mini["address"]:
        raise ValueError("minitoo.address is required")
    audio = raw.get("audio", {})
    if not isinstance(audio, dict) or set(audio) - {"input", "output", "rate"}:
        raise ValueError("audio accepts input, output and rate")
    return raw


def sdk_config(config: dict) -> dict:
    """Create the config expected by Hermes Gadget's Linux Client."""
    out = {"server": config["server"], "name": config.get("name", "MiniToo Gadget")}
    if "token" in config:
        out["token"] = config["token"]
    if config.get("audio"):
        out["audio"] = dict(config["audio"])
    # Hermes Gadget's Client only needs Display.width/height/touch/round after
    # construction. Our MiniTooDisplay ignores SDL-specific fields entirely.
    out["display"] = dict(config["minitoo"])
    return out
