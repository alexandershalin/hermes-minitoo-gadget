"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path


def default_state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "hermes-minitoo-gadget"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes-minitoo")
    p.add_argument("--state-dir", type=Path, default=default_state_dir())
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run MiniToo as a Hermes Gadget Linux device")
    run.add_argument("--config", type=Path, required=True)
    sub.add_parser("status")
    sub.add_parser("messages")
    send = sub.add_parser("send")
    send.add_argument("text")
    button = sub.add_parser("button")
    button.add_argument("button", choices=("talk", "cancel", "up", "down"))
    button.add_argument("state", choices=("press", "release"))
    sub.add_parser("audio-devices")
    return p


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "run":
            from .runtime import run
            run(args.config, args.state_dir)
            return 0
        if args.command == "audio-devices":
            from hermes_gadget.linux.audio import backend
            print(backend().query_devices())
            return 0
        from hermes_gadget.linux.control import request
        message = {"command": args.command}
        if args.command == "send":
            message["text"] = args.text
        elif args.command == "button":
            message.update(button=args.button, pressed=args.state == "press")
        result = request(args.state_dir, message)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if "error" in result else 0
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        print(f"hermes-minitoo: {exc}")
        return 1
