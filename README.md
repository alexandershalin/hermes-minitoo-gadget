# Hermes MiniToo Gadget

Experimental Linux adapter that turns a **Divoom MiniToo** into the display and speaker for a **Hermes Gadget** device.

The project deliberately keeps **Hermes Gadget SDK as the core**. Pairing, Hermes protocol, conversation state, native renderer, audio streaming and the Linux control socket come from Hermes Gadget. This repository adds MiniToo-specific Bluetooth/display adaptation around that core.

## Status

Early development / hardware-unverified on this project's Linux target.

Implemented now:

- Hermes Gadget SDK `v0.2.0` is the runtime core.
- Hermes Gadget's native framebuffer is exported to MiniToo through Bluetooth Classic RFCOMM/SPP.
- The current compatibility backend renders `128x128` RGB888 and uses the community-documented Zstandard image path.
- Display uploads run on a worker thread so Bluetooth transfer latency does not block the Hermes Gadget event loop.
- Hermes Gadget's existing Linux audio backend can target MiniToo as a Bluetooth speaker.
- Existing Hermes Gadget local control socket is reused for `status`, `send`, and button commands.

Not yet physically verified here:

- simultaneous RFCOMM display traffic + A2DP audio on the Linux host;
- exact BlueZ/PipeWire audio device naming;
- sustained refresh rate and reconnect behavior;
- the newer native `160x128` lossless live-frame path.

## Research archive

MiniToo reverse engineering is moving quickly, and this project is intended to grow beyond the first Hermes display backend.

Start here:

- [Research index](research/README.md)
- [Known MiniToo hacks and protocol capabilities](research/HACKS.md)
- [Firmware / OTA / custom-code research](research/FIRMWARE.md)
- [Source map](research/SOURCES.md)
- [Firmware asset manifest placeholder](research/firmware/MANIFEST.md)

The repository also exposes a machine-readable status registry:

```bash
hermes-minitoo capabilities
```

A capability marked `planned`, `research`, or `blocked` is **not implemented by this project yet**, even if another community project has demonstrated it.

## Architecture

```text
Hermes Agent
    │
    │ Hermes Gadget protocol / WebSocket
    ▼
Hermes Gadget Linux client (SDK v0.2.0)
    │
    ├── native Hermes Gadget renderer ──► MiniTooDisplay
    │                                      │
    │                                      └── RFCOMM/SPP ──► MiniToo LCD
    │
    └── Hermes Gadget audio output ──────► PortAudio/PipeWire ──► MiniToo speaker
```

The adapter currently hooks Hermes Gadget's Linux `Display` class at process startup. That is intentionally small and keeps the upstream Linux `Client` unchanged. A cleaner pluggable display-backend API can later be proposed upstream.

## Install from a checkout

Linux with Bluetooth support, Python 3.10+, Git, a compiler/CMake required by the Hermes Gadget native core, PortAudio, and BlueZ are expected.

```bash
sudo apt update
sudo apt install git python3-venv cmake build-essential libportaudio2 bluez

python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

hermes-gadget build-sim --test
```

Pair MiniToo with Linux using normal BlueZ tools first. Then:

```bash
cp examples/config.example.json config.json
$EDITOR config.json
hermes-minitoo run --config config.json
```

In another terminal:

```bash
hermes-minitoo status
hermes-minitoo send "Hello from MiniToo"
```

Approve the normal Hermes Gadget pairing code on the Hermes host with:

```bash
hermes gadget approve CODE
```

## Current display path vs. the better future path

The first backend was intentionally based on the already-working `128x128` RGB888 + Zstandard `0x8B` path documented by the early MiniToo reverse-engineering work.

Newer independent work has demonstrated a better **native 160×128 lossless live path**, using a `0x23` animation payload, MiniLZO-compressed RGB888, cell dimensions `08 0A`, a ready ACK, and 256-byte `0x8B` chunks. That path is now the preferred future backend and is tracked as a planned capability rather than silently changing the initial implementation before Linux hardware verification.

See [research/HACKS.md](research/HACKS.md).

## Example configuration

```json
{
  "server": "ws://127.0.0.1:8765/gadget",
  "name": "MiniToo Gadget",
  "minitoo": {
    "address": "AA:BB:CC:DD:EE:FF",
    "channel": 1,
    "max_fps": 2.0,
    "packet_delay_ms": 12
  },
  "audio": {
    "output": "Divoom MiniToo",
    "rate": 48000
  }
}
```

`audio` is passed to Hermes Gadget's Linux audio implementation. A future external microphone can be added as `audio.input` without changing the display adapter.

## License and attribution

No project license has been selected yet. Hermes Gadget SDK remains under its own MIT license; other dependencies and research projects keep their respective licenses.

No Divoom firmware, app binaries, decompiled sources, vendor datasheets, keys, or account data are redistributed here. Protocol notes are an independent research index pointing to public work.
