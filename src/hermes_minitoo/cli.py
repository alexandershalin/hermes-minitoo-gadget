"""Command-line interface.

Two entry points share one set of commands:

* ``hermes-gadget minitoo ...`` - the MiniToo client as a sibling of ``hermes-gadget linux``.
  The SDK has no plugin hook for subcommands, so :func:`gadget_main` builds the SDK parser,
  adds ``minitoo`` to it and leaves every other SDK command untouched.
* ``hermes-minitoo ...`` - the original name, kept as an alias (existing services use it).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path


def default_state_dir() -> Path:
    # Deliberately not "hermes-gadget": device identity and pairing live here, renaming forces a re-pair.
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "hermes-minitoo-gadget"


def _common(p: argparse.ArgumentParser, *, top: bool) -> None:
    # Accepted both before and after the subcommand; the subparser copies use
    # SUPPRESS so they never overwrite a value given before the subcommand.
    p.add_argument("--state-dir", type=Path,
                   default=default_state_dir() if top else argparse.SUPPRESS)
    p.add_argument("--verbose", action="store_true",
                   default=False if top else argparse.SUPPRESS)


def _add_commands(sub) -> None:
    """Commands shared by ``hermes-gadget minitoo`` and ``hermes-minitoo``."""

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, **kwargs)
        _common(sp, top=False)
        return sp

    run = add("run", help="Run MiniToo as a Hermes Gadget Linux device")
    run.add_argument("--config", type=Path, required=True)
    add("status", help="Show connection, pairing code and service uptime")
    add("messages", help="Show the last 20 replies and notices")
    add("capabilities", help="Show implemented and research-only MiniToo capabilities")
    send = add("send", help="Send a text message to Hermes")
    send.add_argument("text")
    button = add("button", help="Press or release a device control")
    button.add_argument("button", choices=("talk", "cancel", "up", "down"))
    button.add_argument("state", choices=("press", "release"))
    add("audio-devices", help="List available input and output devices")


def parser() -> argparse.ArgumentParser:
    """Parser of the standalone ``hermes-minitoo`` command."""
    p = argparse.ArgumentParser(prog="hermes-minitoo")
    _common(p, top=True)
    _add_commands(p.add_subparsers(dest="command", required=True))
    return p


def add_parser(sub) -> None:
    """Register ``minitoo`` next to ``linux`` in the SDK's ``hermes-gadget`` parser."""
    p = sub.add_parser("minitoo", help="Run and control a Divoom MiniToo gadget")
    p.add_argument("--state-dir", type=Path, default=default_state_dir())
    _add_commands(p.add_subparsers(dest="minitoo_command", required=True))
    p.set_defaults(func=minitoo_main)


def _execute(command: str, args: argparse.Namespace) -> int:
    try:
        if command == "capabilities":
            from .capabilities import capabilities_dict
            print(json.dumps(capabilities_dict(), indent=2, ensure_ascii=False, sort_keys=True))
            return 0
        if command == "run":
            from .runtime import run
            run(args.config, args.state_dir)
            return 0
        if command == "audio-devices":
            from hermes_gadget.linux.audio import backend
            print(backend().query_devices())
            return 0
        from hermes_gadget.linux.control import request
        message: dict = {"command": command}
        if command == "send":
            message["text"] = args.text
        elif command == "button":
            message.update(button=args.button, pressed=args.state == "press")
        result = request(args.state_dir, message)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if "error" in result else 0
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        print(f"hermes-minitoo: {exc}")
        return 1


def minitoo_main(args: argparse.Namespace) -> int:
    """Handler of ``hermes-gadget minitoo <command>``."""
    return _execute(args.minitoo_command, args)


def _setup_logging(verbose: bool, *, info: bool) -> None:
    level = logging.DEBUG if verbose else (logging.INFO if info else logging.WARNING)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    """Entry point of the legacy ``hermes-minitoo`` command."""
    args = parser().parse_args(argv)
    _setup_logging(args.verbose, info=True)
    return _execute(args.command, args)


def gadget_parser() -> argparse.ArgumentParser:
    """The SDK's ``hermes-gadget`` parser with ``minitoo`` added."""
    from hermes_gadget.cli import build_parser

    p = build_parser()
    sub = next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub is None:  # SDK changed its parser layout: fail loudly instead of silently dropping the command
        raise RuntimeError("cannot find the subcommand group in hermes_gadget.cli.build_parser()")
    add_parser(sub)
    return p


def gadget_main(argv: list[str] | None = None) -> int:
    """Entry point of ``hermes-gadget``: every SDK command plus ``minitoo``."""
    args = gadget_parser().parse_args(argv)
    # The SDK logs at WARNING; the MiniToo service needs INFO (connect / ACK / reconnect lines).
    _setup_logging(bool(getattr(args, "verbose", False)), info=args.command == "minitoo")
    return int(args.func(args) or 0)
