# Divoom MiniToo reverse-engineering map

Snapshot: **2026-10-06**. This is a preservation document, not a claim that every item works in this repository.

## 1. Hardware and Bluetooth surface

**Verified/reported by community projects:**

- Bluetooth control is **Classic RFCOMM/SPP**, not BLE/GATT.
- `Divoom MiniToo-Audio` exposes JL-style services including SPP and standard speaker profiles. Community probes report RFCOMM channels **1** and **10**; channel 1 is the most commonly tested application path.
- The device effectively allows one application RFCOMM owner at a time. A phone running the Divoom app can conflict with a desktop client.
- Standard audio uses the normal Bluetooth speaker stack (A2DP/AVRCP; some SDP dumps also advertise HFP/HID).
- FCC filing **A8I-MINITOO** includes public internal photos.
- Firmware research reports an **Actions ATS2831** application processor plus a **JieLi AC690N** USB-side/bridge component and a **160×128 IPS panel**. Treat the exact CPU-core identity as a research claim until independently confirmed; public notes themselves contain some mixed ARM/CK802 terminology.

**Interesting contradiction to preserve:** retail/spec listings often say “no microphone”, while protocol research reports a working built-in **noise-meter tool** that reacts to ambient sound. The source of that signal should be verified on our physical unit before assuming microphone hardware is available to applications.

## 2. Generic SPP framing

Common modern frame:

```text
01 <len_le16> <command> <body...> <checksum_le16> 02
```

Where:

- `len = body_length + 3`;
- checksum is the 16-bit sum of length bytes, command byte and body;
- modern MiniToo traffic does not use the old Divoom escape layer.

The current project implements this framing in `src/hermes_minitoo/protocol.py`.

## 3. Display paths

### 3.1 Current project path: 128×128 RGB888 + Zstandard

Status: **implemented here, hardware verification pending on Linux**.

Early reverse engineering demonstrated:

- command `0x8B`;
- announce total size, wait/request, then indexed 256-byte chunks;
- 128×128 RGB888;
- Zstandard with a 128 KiB window (`window_log=17`);
- still and multi-frame payloads.

This is the conservative compatibility path used by the first Hermes backend.

### 3.2 Native 160×128 lossless live frames

Status: **verified upstream, planned here**.

The Windows .NET implementation reports a cleaner live path:

```text
0x23
frame_count
delay_be16
08 0A
00
lzo_length_be32
MiniLZO1X(RGB888 160×128)
```

Then the payload is transferred with `0x8B`:

- announce: `00 <total_u32_le>`;
- wait for ready response matching `8B 55 00 01`;
- chunks: `01 <total_u32_le> <index_u16_le> <up to 256 bytes>`;
- keep one persistent RFCOMM session;
- serialize complete uploads;
- paced chunks (the reference uses ~5 ms);
- do not flood the firmware.

Why this matters: it preserves native **160×128** geometry and avoids the live JPEG checkerboard artifacts reported around sharp text.

This is the preferred future Hermes display backend.

### 3.3 Native 160×128 RGB/Zstd reports

Status: **reported; needs reconciliation**.

At least one newer MiniToo/Codex project reports native 160×128 lossless RGB/Zstandard output. This differs from the independently verified MiniLZO path above. Do not merge codec assumptions until packet captures or source comparison reconcile the two formats/firmware variants.

### 3.4 JPEG / file / gallery paths

Status: **verified upstream but not suitable as the main live UI path**.

Several distinct state machines exist:

- live animation `0x8B`;
- local-picture/file path around `0x8F`;
- photo/custom-face flows including `0x8D` and `0xBE`;
- JPEG-backed payloads.

Important lesson: `Draw/LocalEq` + `0x8F` can put the device into a real **LOADING** state and is the wrong mechanism for frequent dashboard refreshes.

## 4. Persistent custom faces and fast state switching

Status: **verified upstream, planned here**.

Useful pattern for agent states:

1. upload a few persistent custom GIF/faces once;
2. switch instantly by real `ClockId` using `Channel/SetClockSelectId`;
3. avoid retransmitting a full frame for every state change.

Public testing has used real custom face IDs such as **984** and **986** on specific devices/accounts. These values are **not universal constants** and must be discovered for the actual device.

Persistent installation uses a more involved custom-face/file workflow (research projects describe `Channel/SetCustom` and `0xBE`). It should not run in a live refresh loop.

## 5. Brightness, screen and audio-control commands

### Brightness

Status: **verified upstream**.

JSON:

```json
{"Command":"Channel/SetBrightness","Brightness":50}
```

Binary legacy opcode `0x32` with one value byte is also reported working and is useful for low-latency control.

### Screen on/off

Status: **verified upstream**.

JSON:

```json
{"Command":"Channel/OnOffScreen","OnOff":0}
{"Command":"Channel/OnOffScreen","OnOff":1}
```

Extended `0xBD / 0x2F` screen-control variants are also mapped. Off/on preserves the underlying view.

### Volume and play/pause

Status: **app-mapped/reported; needs our verification**.

Under extended command dispatcher `0xBD`:

- ext `0x34` — set volume;
- ext `0x36` — music play/pause.

For Hermes audio, normal OS A2DP volume may be simpler and safer than proprietary commands.

## 6. Built-in tools

Status: **verified upstream**.

Opcode `0x72` enters native tools:

- `0` stopwatch;
- `1` scoreboard;
- `2` noise meter;
- `3` countdown.

Warnings:

- stopwatch/countdown paths can produce loud alarms;
- “off” often clears values but does **not** leave the tool view;
- research reports no reliable software “return to default clock face” command. Hardware button/reboot is the dependable reset.

Potential Hermes use: native scoreboard/noise views as novelty actions, but they are lower priority than the normal renderer.

## 7. Built-in views and games

Status: **verified upstream**.

Known view switches include:

- `{"Command":"Photo/Enter"}` — photo slideshow;
- `{"Command":"Lyric/Enter"}` — animated astronaut/lyric view;
- binary `0xA0` — built-in games/Tetris variants.

Game exit returns to the previous mode, not necessarily the normal clock.

## 8. Photo albums

Status: **partially verified upstream / complex**.

Documented commands include:

- `Photo/NewAlbum`;
- `Photo/LocalAddToAlbum`;
- `Photo/DevicePhotoToAlbum`;
- local picture/file transfers.

This may eventually be useful for storing Hermes artwork or offline screens, but custom-face slots are probably more useful for low-latency agent states.

## 9. Notifications

Status: **verified upstream for basic notification path; experimental and crash-prone around custom icons**.

Community work reports ANCS-style short **text + built-in icon** notifications working end-to-end.

Do **not** probe arbitrary notification-image paths casually: one tested custom-icon flow around `0x3C SPP_SET_ANCS_NOTICE_PIC` is documented as crashing/rebooting the device.

Future Hermes use: short alerts when a task finishes, approval is needed, or an agent requires attention.

## 10. Queries and broadcasts

Reported responses include:

- `Device/GetStorageStatus`;
- `WhiteNoise/Get`.

Background traffic includes keepalives and Tomato/Pomodoro state broadcasts.

Many plausible JSON GET commands simply do not respond on MiniToo even if they exist in the Android app. Treat Android enum presence as evidence of a code path, **not** proof MiniToo implements it.

## 11. White noise / Pomodoro / other app features

The app and firmware expose traces of:

- multiple white-noise channels;
- Tomato/Pomodoro state;
- timers/tools;
- gallery/photo subsystems.

Most write semantics remain incompletely mapped. Preserve these as research targets rather than public APIs.

## 12. External integrations already demonstrated

Public projects show MiniToo used as:

- Claude Code status display;
- Codex status/quota display;
- Home Assistant display;
- occupancy/sensor status terminal;
- Windows “screen”/dashboard target;
- macOS persistent RFCOMM display daemon.

There is also Home Assistant work that layers MiniToo-specific image transfer over generic Divoom transport and ESP32 proxy projects, suggesting a future route for **remote Bluetooth bridging** when Hermes and MiniToo are not physically co-located.

## 13. Candidate Hermes roadmap

The useful order for this project is:

1. physically verify current Linux RFCOMM + A2DP coexistence;
2. replace the compatibility renderer with native 160×128 lossless live frames;
3. add brightness/screen power as Hermes Gadget actions;
4. add persistent custom-face install/discovery + instant ClockId switching for agent states;
5. add notification action;
6. investigate photo storage/gallery;
7. only then consider firmware-level work.

See `hermes-minitoo capabilities` and the GitHub issues for tracked placeholders.
