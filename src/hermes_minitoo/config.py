"""Configuration validation and translation to Hermes Gadget Linux config."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

_ALLOWED = {"server", "name", "token", "audio", "minitoo"}
_MINITOO_ALLOWED = {
    "address", "channel", "update_interval_ms", "chunk_delay_ms",
    "ready_timeout_ms", "reconnect_delay_ms", "max_payload_bytes",
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

    numeric_ranges = {
        "channel": (1, 30, 1),
        "update_interval_ms": (250, 60000, 2500),
        "chunk_delay_ms": (0, 100, 5),
        "ready_timeout_ms": (100, 30000, 8000),
        "reconnect_delay_ms": (0, 60000, 2000),
        "max_payload_bytes": (1024, 600000, 600000),
    }
    for key, (low, high, default) in numeric_ranges.items():
        value = mini.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"minitoo.{key} must be an integer from {low} to {high}")

    audio = raw.get("audio", {})
    if not isinstance(audio, dict) or set(audio) - {"input", "output", "rate"}:
        raise ValueError("audio accepts input, output and rate")
    return raw


def sdk_config(config: dict) -> dict:
    out = {"server": config["server"], "name": config.get("name", "MiniToo Gadget")}
    if "token" in config:
        out["token"] = config["token"]
    if config.get("audio"):
        out["audio"] = dict(config["audio"])
    out["display"] = dict(config["minitoo"])
    return out
