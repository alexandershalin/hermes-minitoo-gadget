# Notices

This project depends on Hermes Gadget SDK by its contributors:
https://github.com/Adolanium/hermes-gadget-sdk

Hermes Gadget SDK is licensed under the MIT License. The device core, WebSocket transport and pairing protocol are imported from the installed package. `client.py`, `control.py` and `audio.py` follow the structure of `hermes_gadget.linux` (`client.py`, `control.py`, `audio.py`) and contain code adapted from it, under the same MIT licence (Copyright (c) 2026 Hermes Gadget SDK contributors). The rest of this repository imports and extends the installed package at runtime.

MiniToo protocol behavior was implemented using public reverse-engineering notes from:
https://github.com/alvinunreal/divoom-minitoo-osx/blob/main/PROTOCOL.md

No Divoom firmware, application binaries, keys, or proprietary source code are included.
