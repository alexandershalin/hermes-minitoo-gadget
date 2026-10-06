# Hermes MiniToo Gadget

Experimental Linux adapter that turns a **Divoom MiniToo** into the display and speaker for a **Hermes Gadget** device.

The project deliberately keeps **Hermes Gadget SDK as the core**. Pairing, Hermes protocol, conversation state, native renderer, audio streaming and the Linux control socket come from Hermes Gadget. This repository adds a MiniToo display backend and configuration/launcher glue.

## Status

Early development / hardware-unverified.

Implemented in the first milestone:

- Hermes Gadget SDK `v0.2.0` is the runtime core.
- Native Hermes Gadget framebuffer is rendered at `128x128` for MiniToo.
- RGB565 framebuffer is converted to MiniToo's RGB888 image payload.
- Image payload is Zstandard-compressed with the Android-compatible 128 KiB window (`window_log=17`).
- MiniToo image transfer uses Bluetooth Classic RFCOMM/SPP channel 1.
- Display uploads run on a background thread so a slow Bluetooth transfer does not block the Hermes Gadget event loop.
- Hermes Gadget's existing Linux audio backend can target MiniToo as a Bluetooth speaker.
- Existing Hermes Gadget local control socket is reused for `status`, `send`, and button commands.

Not yet physically verified on Alexander's MiniToo/Linux host:

- simultaneous RFCOMM display traffic + A2DP audio;
- exact BlueZ/PipeWire audio device naming;
- sustained refresh rate and compression tuning;
- automatic Bluetooth pairing/reconnect UX.

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
    │                                      └── RFCOMM/SPP ch.1 ──► MiniToo LCD
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

# Hermes Gadget v0.2.0 supplies the native core. Build it once if your install
# does not already contain a compatible native library:
hermes-gadget build-sim --test
```

Pair MiniToo with Linux using normal BlueZ tools first. Then copy the example config:

```bash
cp examples/config.example.json config.json
$EDITOR config.json
```

Run:

```bash
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

`audio` is passed to Hermes Gadget's Linux audio implementation. A future USB microphone can be added as `audio.input` without changing this adapter.

## Why 128×128?

Reverse engineering of the MiniToo application protocol shows a stable `128x128` RGB888 still/animation format (`8 x 8` blocks of 16 pixels). The physical LCD and the application protocol should not be conflated: this adapter targets the known-good application image path.

## Upstream and protocol references

- Hermes Gadget SDK: <https://github.com/Adolanium/hermes-gadget-sdk>
- MiniToo protocol research: <https://github.com/alvinunreal/divoom-minitoo-osx/blob/main/PROTOCOL.md>

No Divoom firmware is redistributed. MiniToo protocol framing in this project is an independent implementation based on publicly documented reverse engineering.

## License

No project license has been selected yet. Hermes Gadget SDK remains under its own MIT license; other dependencies keep their respective licenses.
