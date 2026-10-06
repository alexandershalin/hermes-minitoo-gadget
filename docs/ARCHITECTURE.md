# Architecture

## Principle

Hermes Gadget SDK remains the device runtime. This repository should contain only MiniToo-specific adaptation.

## Current integration seam

Hermes Gadget `v0.2.0` constructs `hermes_gadget.linux.display.Display` lazily from the Linux `Client`. At startup, `hermes-minitoo` replaces that class with `MiniTooDisplay`, then runs the unmodified upstream Linux control/service loop.

This is intentionally a compatibility shim, not a fork.

## Display pipeline

1. Hermes Gadget native C++ core renders its normal UI into RGB565.
2. `MiniTooDisplay.present()` copies the 128×128 framebuffer.
3. RGB565 LE is converted to RGB888.
4. Only the latest pending frame is retained.
5. A worker thread Zstd-compresses and sends the image through MiniToo's RFCOMM application channel.

The background worker prevents Bluetooth transfer latency from blocking WebSocket traffic, audio handling, controls, or the Hermes Gadget core clock.

## Audio pipeline

No proprietary MiniToo audio protocol is required. Hermes Gadget's existing Linux PortAudio backend is configured with MiniToo's system Bluetooth audio sink. On a modern Linux host that will normally be provided by PipeWire/WirePlumber or PulseAudio compatibility.

## Next milestones

1. Physical Linux/MiniToo smoke test: RFCOMM image only.
2. Verify persistent RFCOMM reconnect behavior.
3. Verify RFCOMM and A2DP can remain active simultaneously on BlueZ.
4. Identify the exact PortAudio/PipeWire device name and supported sample rate.
5. Add a systemd unit and install helper after the first physical smoke test.
6. If the shim proves stable, propose a generic pluggable Linux display backend API to Hermes Gadget upstream.
