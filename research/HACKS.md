# Divoom MiniToo reverse-engineering map

Snapshot: **2026-10-06**. Preservation document; not every item is supported by this repository.

## 1. Hardware and Bluetooth surface

Community work reports Bluetooth Classic RFCOMM/SPP control, standard Bluetooth speaker profiles, a 160×128 IPS panel, and one application RFCOMM owner at a time. Channels 1 and 10 are commonly observed, with channel 1 the primary tested path.

FCC filing **A8I-MINITOO** contains public internal photos.

Firmware research reports an Actions ATS2831 application processor and JieLi AC690N-related component. Treat exact CPU/debug-core terminology as research until independently confirmed.

## 2. Generic SPP framing

```text
01 <len_le16> <command> <body...> <checksum_le16> 02
```

`len = body_length + 3`. Checksum is the 16-bit byte sum of length, command and body.

Implemented in `src/hermes_minitoo/protocol.py`.

## 3. Display paths

### 3.1 Legacy path: 128×128 RGB888 + Zstandard

Status: **verified by earlier community work; no longer used by this runtime**.

Early reverse engineering demonstrated `0x8B`, 256-byte chunks, 128×128 RGB888 and Zstandard with a 128 KiB window. This was the project's first backend and remains useful for comparison and firmware-family research.

### 3.2 Native 160×128 lossless live frames

Status: **implemented here; upstream real-device verified; Linux hardware validation pending**.

Payload:

```text
23
01
<frame_delay_u16_be>
08 0A
00
<lzo_length_u32_be>
<complete LZO1X RGB888 stream>
```

Rules:

- input is exactly 160×128×3 = 61,440 row-major RGB888 bytes;
- encoding marker `00` means lossless MiniLZO/LZO1X;
- `08 0A` are 16-pixel cell dimensions, not literal pixel dimensions;
- complete LZO stream ends `11 00 00`;
- this project locally decompresses every compressed frame and compares all source bytes before sending.

Transfer over `0x8B`:

- announce `00 <total_u32_le>`;
- require ready response `8B 55 00 01`;
- chunks `01 <total_u32_le> <index_u16_le> <up to 256 bytes>`;
- persistent connection and serialized uploads;
- conservative 5 ms chunk pacing and 600 KB payload ceiling.

Reference: `sirnugget11/divoom-minitoo-dotnet`.

### 3.3 Native 160×128 RGB/Zstd reports

Status: **research**.

Some newer projects report native-resolution RGB/Zstandard variants. This may reflect a different payload builder or firmware path. Do not mix codec formats without packet-level reconciliation.

### 3.4 JPEG / file / gallery paths

Multiple other state machines exist: local-picture `0x8F`, photo/custom-face paths around `0x8D`/`0xBE`, and JPEG payloads. `Draw/LocalEq` + `0x8F` can enter a visible LOADING state and is wrong for frequent live updates.

## 4. Persistent custom faces and fast state switching

Status: **verified upstream, planned here**.

Upload persistent custom faces once, then switch rapidly using their real `ClockId` with `Channel/SetClockSelectId`. IDs such as 984/986 seen in examples are device/account-specific and must not be hard-coded as universal values.

## 5. Brightness, screen and audio-control commands

Verified brightness routes include JSON `Channel/SetBrightness` and binary opcode `0x32`. Screen on/off works through JSON and an extended `0xBD/0x2F` path while preserving the underlying view.

Extended command mappings also report volume and play/pause controls. Hermes speech should still prefer normal OS A2DP unless proprietary control adds value.

## 6. Built-in tools

Opcode `0x72` exposes stopwatch, scoreboard, noise meter and countdown. Stopwatch/countdown can produce loud alarms. Tool “off” may not leave the tool view, and hardware button/reboot is the dependable return path.

## 7. Built-in views and games

Reported/verified switches include photo slideshow, lyric/astronaut view and built-in games through binary `0xA0`.

## 8. Photo albums

Research maps `Photo/NewAlbum`, `Photo/LocalAddToAlbum`, `Photo/DevicePhotoToAlbum` and local file transfer paths. Potential future use: persistent Hermes art/offline screens.

## 9. Notifications

Basic ANCS-style text + built-in-icon notifications have been demonstrated. Avoid arbitrary custom notification-image probing: at least one `0x3C` custom-icon path is documented as crashing/rebooting the device.

## 10. Queries and broadcasts

Reported responses include `Device/GetStorageStatus` and `WhiteNoise/Get`. Background traffic includes keepalives and Tomato/Pomodoro state. Many plausible app JSON commands do not respond on MiniToo.

## 11. External integrations already demonstrated

Public projects use MiniToo as Claude/Codex status displays, Home Assistant terminals, sensor/occupancy displays and desktop dashboards. Home Assistant + ESP32 proxy work suggests a future remote Bluetooth bridge when Hermes and MiniToo are not co-located.

## 12. Candidate Hermes roadmap

1. physically verify native 160×128 Linux path;
2. verify RFCOMM + A2DP coexistence;
3. add brightness/screen actions;
4. add custom-face discovery/switching;
5. add notifications;
6. investigate photo storage;
7. only then consider firmware-level work.
