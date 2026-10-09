# display-link

## SUMMARY

1. The log line "did not send the 0x8B ready ACK" comes from a TimeoutError. It is raised only when the RFCOMM DLC stays open but the speaker's application layer sends no `8B 55 00 01` within ready_timeout (transport.py:119-135). A real DLC drop would show up as EOF (ConnectionError) or an OSError. So on a normal switch the RFCOMM channel is most likely not "torn": the MiniToo app layer goes silent. Our own code then closes its DLC (transport.py:159-162) and enforces a 2 s backoff, so part of the "break" is self-inflicted.
2. Linux multiplexes PipeWire's HFP AT-command DLC and our SPP DLC over one rfcomm_session per (adapter, peer) (net/bluetooth/rfcomm/core.c:386-391, 730-756). HFP works during recording, so routine switches do not kill the shared session. PipeWire's "lost RFCOMM connection" (backend-native.c:2549-2552) marks the rarer total losses described in 5.7.
3. The switch sequence is proven by source. The talk press makes the SDK open the mic before it sets the Listening mode (hermes-gadget-sdk 323e330 app.cpp:828/847, audio.py:73-92). WirePlumber switches the profile a hard-coded 500 ms after a capture link appears (autoswitch-bluetooth-profile.lua:34-35; identical in 0.5.12-0.5.14). PipeWire then releases A2DP, which BlueZ turns into an AVDTP SUSPEND only if the stream is streaming (a2dp.c:3381-3420). PipeWire creates the SCO nodes and connects a raw SCO socket (backend-native.c:2593-2690). The kernel forces active mode and sets up eSCO using its S3/S2/S1 (CVSD) or T2/T1 (mSBC) parameter tables (hci_conn.c:52-68, 1789-1797). PipeWire also sends a dummy "+CIEV: 2,1" call-active indicator (backend-native.c:1067-1081, 2809-2810).
4. Bandwidth does not explain the stall. eSCO EV3/2-EV3 takes 2-4 of every 6-12 slots, which still leaves hundreds of kbit/s for ACL. The display needs about 25 kbit/s on average and about 200 kbit/s in a burst. ACL and SCO on one link coexist routinely, and nothing in BlueZ/PipeWire/kernel closes RFCOMM on SCO setup.
5. The most likely cause is MiniToo firmware behaviour [HYPOTHESIS]: its app task does not service 0x8B while it enters HFP "call" mode, possibly because of the dummy call indicator. A 60 s steady-HFP diagnostic decides whether the stall covers the whole HFP period or only the transitions.
6. The current code makes the problem worse:
- The 2.5 s throttle (display.py:82) delays a Listening frame by 0-2.5 s (mean about 1.4 s, derived from the Ready blink cadence). That is almost always after the 500 ms switch trigger.
- A single unanswered announce blocks the worker for 8 s. With close, the 2 s backoff and the 1 s retry sleep, one cycle takes about 10-11 s.
- Listening, Thinking and Responding are animated (ui.cpp:61-99, 100 ms clock), so a new frame is queued every 2.5 s for the whole HFP period. The storm lasts as long as the device is silent, and the correct frame can still arrive up to about 10 s late.
- "recovered after 2-7 s" is measured from the start of the newer frame, so it hides the abandoned frame's 8 s wait.
7. The f11ae6a experiment could not have worked. It waited after the press, when the mic was already open, and its "ACK" only meant the chunks were written to the socket.
8. Delivering Listening over 0x8B during SCO setup is not solvable from the host. Delivering it before SCO is possible only by deferring the Gadget mic until the frame is uploaded. That costs about +0.2-0.5 s, capped at 1.5 s. The user earlier said "leave it as is", so this is opt-in only.
9. 8087:0a2a is the Bluetooth part of an Intel Wireless 7265 or 3165 combo card (Stone Peak, legacy ROM, BT 4.2, Wi-Fi on the same chip). The kernel flags it NO_WBS (btusb.c:492-494, btintel.c:3469-3487). PipeWire's hardware DB has no Intel quirks, and its mSBC decision ignores that flag, so mSBC on this adapter is possible. Check the logs, and test forcing CVSD.
10. Three symptoms are misread in the HANDOFF:
- "corrupted SCO packet" is a USB isochronous reassembly error on the host side (btusb.c:1341-1412, 1665), not a radio error.
- The RSSI values (-58..-85) come only from discovery reports (BlueZ adapter.c:7445-7447), not from the connected link.
- "Unable to disable scanning: -16" means HCI Command Disallowed while discovery or an LE scan is starting (hci_sync.c:2247/6013, lib.c:106). Correlate it with minitoo-autoaddr's 12 s scans.
11. Autosuspend does not persist because btusb re-enables it on every probe (btusb.c:35, 4403-4404). The fix is `options btusb enable_autosuspend=0` plus a udev rule that runs after btusb binds. Both need root.
12. A third-party source documents that over USB the MiniToo is a Jieli 4C4A:4E55 composite device: USB Audio speaker and microphone plus HID consumer-control keys. Bluetooth SPP still drives the screen (divoom-minitoo-tools usb_minitoo.py:1-11). If the speaker can be cabled to the server, that removes A2DP/HFP from Bluetooth altogether, which also covers 5.1.
13. A working prototype of all opt-in mitigations is in scratchpad/proto: HFP gate via the unprivileged HCIGETCONNLIST ioctl plus mic hooks, short ready/chunk timeouts, screen-change bypass, listen_preroll, and the config whitelist fix. The full suite passes (27 tests: the original 21 plus 6 new) and defaults are unchanged. None of this has been tested on hardware.
14. A custom-firmware fix is not feasible. There is no practical flashing route, the SoC identity is disputed, the vendor Bluetooth stack has no symbols or datasheet, and probing has bricked devices before.

## FINDINGS
- [proven-by-source/high] The logged failure "did not send the 0x8B ready ACK" means the DLC was open but the MiniToo app layer stayed silent for ready_timeout (8 s). It is not an RFCOMM disconnect. A remote DISC/EOF would raise ConnectionError("MiniToo closed RFCOMM connection") and socket errors would raise OSError with an errno.
  EVIDENCE: main/src/hermes_minitoo/transport.py:119-135: TimeoutError only on deadline or socket.timeout; `if not data: raise ConnectionError(...)` at 131-132
- [proven-by-source/high] Part of the 'break' is self-inflicted. Any exception inside send_rgb888 sets last_failure and close()s our own DLC; the next attempt is refused for 2 s ('reconnect backoff is active') and then needs a fresh DLC open with a connect timeout of max(1, ready_timeout) = 8 s.
  EVIDENCE: transport.py:159-162 (last_failure + close), 99-101 (backoff), 103 (_open_rfcomm timeout), 104-106
- [proven-by-source/medium] Our SPP DLC and PipeWire's HFP AT-command DLC are multiplexed over one kernel rfcomm_session (one L2CAP PSM 3 channel) to the MiniToo. Closing that session closes every DLC. Recording keeps working, and it needs the HFP SLC, so routine switches do not destroy the session. PipeWire's 'lost RFCOMM connection' is HUP on the HFP DLC, which identifies the rarer total-loss events of 5.7.
  EVIDENCE: linux v7.0 net/bluetooth/rfcomm/core.c:386-391 (__rfcomm_dlc_open reuses rfcomm_session_get(src,dst)), 730-741, 743-756 (rfcomm_session_close closes all dlcs); pipewire 1.6.2 spa/plugins/bluez5/backend-native.c:2549-2552
- [proven-by-source/medium] Reconnect can fail with EBUSY. A closed DLC stays registered in BT_DISCONN until the peer answers DISC or RFCOMM_DISC_TIMEOUT expires (20 s, or 40 s if tx data is queued), and __rfcomm_dlc_open returns -EBUSY while the same DLCI exists. A busy speaker can therefore cause '[Errno 16] Device or resource busy' on reconnect.
  EVIDENCE: rfcomm/core.c:393-396 (DLCI exists -> -EBUSY), 437-448 (__rfcomm_dlc_disconn timers); include/net/bluetooth/rfcomm.h:30 RFCOMM_DISC_TIMEOUT (HZ*20)
- [proven-by-source/high] The talk press itself starts the profile switch. SDK start_listening() calls hal_.mic->start() before mode_=Listening; Linux Audio.mic_start opens a PortAudio RawInputStream on audio.input (the WirePlumber bluez loopback source); WirePlumber switches to headset-head-unit 500 ms after a capture stream links to that loopback, and restores A2DP 2000 ms after the last link is gone. Both constants are hard-coded.
  EVIDENCE: hermes-gadget-sdk v0.2.0 (323e330) firmware/core/src/app.cpp:825-848 (mic->start at 828, mode_ at 847); python/hermes_gadget/linux/audio.py:73-92; wireplumber src/scripts/device/autoswitch-bluetooth-profile.lua:34-35, 248-293; same values in https://github.com/PipeWire/wireplumber/blob/0.5.13/src/scripts/device/autoswitch-bluetooth-profile.lua#L34-L35 and 0.5.12 lines 35-36
- [proven-by-source/high] The Listening frame is almost never queued before the switch. present() queues at most once per update_interval_ms=2500. In Ready (hero mode) the screen redraws only on the mascot blink, so frames are queued at t, t+2.5, t+4.5, ... A press therefore always hits the throttle, with a 0-2.5 s delay (mean about 1.36 s), which is later than the 500 ms switch trigger in nearly all cases.
  EVIDENCE: display.py:33, 82; sdk ui.cpp:84-99 (hero_anim_key: Ready changes only with mascot_frame, frame%45<2), app.cpp:1492-1501 (Ready hero), client.py:280-281 (display_flush -> dirty); delay distribution derived
- [proven-by-source/high] Retry storm mechanics. Listening, Thinking and Responding are animated (100 ms frame clock), so a fresh frame is queued every 2.5 s for the whole HFP period. Each real attempt can block 8 s (ready wait or connect), then 2 s backoff with 1 s retry sleeps, giving about 10-11 s per blocking cycle. Each frame gets retry_window_s=60, after which it is re-queued. The storm therefore lasts as long as the device is silent (HFP period about 8-16 s: prompt about 2.2 s + VAD recording + release about 0.5-1 s + 2 s WirePlumber restore + A2DP restart), and after recovery the current frame can still be up to about 10 s late.
  EVIDENCE: display.py:99-132 (sleep 1.0 at 132; pending check 129-131; window 122-127); transport.py:99-106, 119-135; sdk ui.cpp:61-73, 91-99; app.cpp:29, 1329-1331
- [proven-by-source/high] The logged 'recovered after N retries (X s)' measures from the start of the current (newer) frame. The abandoned Listening frame's 8 s wait is not included, so real outages are about 8 s longer than the logged 2-7 s.
  EVIDENCE: display.py:108 (started per frame), 113-115, 129-131 (break when newer frame pending)
- [proven-by-source/high] retry_window_s cannot be configured. load_config rejects any minitoo key outside the whitelist, and retry_window_s is not in it, so adding it to config.json makes the service fail at startup.
  EVIDENCE: config.py:10-13, 27-28 vs display.py:35
- [proven-by-source/high] Commit f11ae6a could not succeed by design. Its wait began after 'button talk press', when the SDK had already opened the mic and the switch was underway. Its 'ACK' file was written when send_rgb888 returned, which only means the chunks were written to the kernel socket, not that the frame was rendered.
  EVIDENCE: git show f11ae6a (display.py ACK_FILE after send_rgb888; talk-key waits after button('press')); transport.py:152-158; app.cpp:828
- [hypothesis/low] The device emits `8B 55 01 <idx>` per chunk. The dotnet library treats the analogous `BE 55 01 <idx>` as a resend request. Our transport never reads during the chunk loop, so if `8B 55 01` is also a resend request, chunks lost around a switch are never resent and the frame stays incomplete.
  EVIDENCE: ext/bugzmanov/FINDINGS.md:1065, 1079-1080; ext/divoom-minitoo-dotnet/src/DivoomMiniToo/DivoomClient.cs:262-266, 494-503; transport.py:155-158
- [proven-by-source/high] What PipeWire 1.6.2 does on a2dp-sink -> headset-head-unit:
- set_profile removes the nodes and releases the A2DP transport; BlueZ sends AVDTP SUSPEND only if the stream is STREAMING, and does nothing on air if it is OPEN; the AVDTP/L2CAP channels stay up.
- It ensures the HFP codec; if the codec was already negotiated at SLC setup there is no air traffic, otherwise it sends +BCS with a 20 s timeout.
- It emits the SCO source and sink nodes.
- On acquire it opens a raw BTPROTO_SCO socket (BT_VOICE_TRANSPARENT for non-CVSD) and connects non-blocking.
- The kernel exits sniff first (BT_POWER_FORCE_ACTIVE_ON) and then sends HCI Enhanced Setup Synchronous Connection.
- No host-initiated role switch happens in this path.
  EVIDENCE: bluez5-device.c:1405-1427, 1431-1530 (1451 release, 1509 ensure_hfp_codec), 1364-1376; bluez 5.85 profiles/audio/transport.c:432-447, a2dp.c:3381-3420; backend-native.c:56-57, 3335-3368, 2593-2690, 2791-2826; linux hci_conn.c:1789-1797, 287-430
- [proven-by-source/high] Kernel eSCO parameter sets used: CVSD tries S3 (2-EV3 allowed, max latency 10 ms, RE=power) -> S2 (7 ms) -> S1 (EV3, 7 ms) -> D1/D0 (HV3/HV1); mSBC tries T2 (2-EV3, 13 ms, RE=quality) -> T1 (EV3, 8 ms). S4 is never requested even though the MiniToo advertises it.
  EVIDENCE: linux v7.0 net/bluetooth/hci_conn.c:52-68
- [hypothesis/medium] While SCO is up, PipeWire tells the MiniToo a call is active with '+CIEV: 2,1' (and '+CIEV: 2,0' on release) unless bluez5.disable-dummy-call is set. The speaker therefore enters its HFP 'call active' state on every recording, which is a plausible trigger for firmware behaviour changes such as an SPP stall or buttons being remapped to call control (5.1).
  EVIDENCE: backend-native.c:1067-1081, 2808-2810, 2864-2865; bluez5-device.c:3109-3112, 3380-3381; pipewire doc/dox/config/pipewire-props.7.md:1146-1150
- [documented-by-third-party/medium] ACL capacity with eSCO is ample. Packet payloads (Core Spec Vol 2 Part B): EV3 30 B / 2-EV3 60 B, 1 slot; DH5 339 B, 2-DH5 679 B, 2-DH3 367 B, 2-DH1 54 B. Derived capacity:
- 2-EV3 at Tesco=12 with WeSCO<=2 leaves 8-10 free slots (one 2-DH5 per 7.5 ms, about 700 kbit/s).
- EV3 at Tesco=6 leaves 2-4 free slots (about 115-780 kbit/s).
The display needs about 25 kbit/s on average (8 KB per 2.5 s) and about 200 kbit/s for a 0.3 s burst. Bandwidth cannot explain seconds of silence.
  EVIDENCE: Bluetooth Core Specification v5.x Vol 2 Part B §6.5 packet tables and §8.6.3 eSCO (documented); arithmetic is mine
- [proven-by-source/medium] Payload and timing estimate for the Listening upload, measured with my own LZO1X-1 benchmark on synthetic 160x128 frames:
- flat 304 B (2 chunks)
- typical UI 3,422-7,684 B (14-31 chunks)
- dense UI with text: 7.7 KB (31 chunks)
- incompressible noise 61,696 B (241 chunks)
The 5 ms pacing alone is 65-150 ms typical and 1.2 s worst. The pure-Python RGB565->888 loop took 14 ms on a 2.8 GHz Xeon (estimated 40-80 ms on an N3700). Upstream measured 259-416 ms per 14-15-chunk upload.
  EVIDENCE: scratchpad/bench/lzo_bench.py output; codec.py:88-123; protocol.py:8, 87-106; ext/divoom-minitoo-dotnet/docs/live-streaming-without-spinner-or-checkerboard.md:258-260
- [documented-by-third-party/high] 8087:0a2a is the Bluetooth part of an Intel Dual Band Wireless-AC 7265/3165 combo card (Stone Peak, legacy ROM, BT 4.2) with Wi-Fi on the same chip and antennas. Linux flags it COMBINED | NO_WBS_SUPPORT | BROKEN_SHUTDOWN_LED, and btintel says only SdP (0aa7, 3168) among legacy ROM parts supports WBS.
  EVIDENCE: linux v7.0 drivers/bluetooth/btusb.c:492-494, 4227-4228; btintel.c:3469-3487; https://bugs.launchpad.net/intel/+bug/1188096 (Stone Peak = 7265); https://bbs.archlinux.org/viewtopic.php?id=271459 (0a2a on 3165); https://lore.kernel.org/all/s5h4k7qtakt.wl-tiwai@suse.de/T/ (0a2a initial-cmd workaround discussion)
- [proven-by-source/medium] PipeWire may still negotiate mSBC on this non-WBS Intel adapter:
- bluez-hardware.conf has no Intel adapter rule, so generic USB keeps msbc and msbc-alt1.
- device_supports_codec bases the decision on quirks plus LMP transparent-SCO/eSCO feature bits, not on the kernel WBS flag.
- The kernel SCO socket accepts BT_VOICE_TRANSPARENT whenever enhanced sync is supported; the WBS flag only controls erroneous-data reporting.
- btusb falls back to USB alt 1 for transparent SCO.
Whether mSBC is actually used depends on the MiniToo's AT+BAC list and must be checked in logs.
  EVIDENCE: pipewire spa/plugins/bluez5/bluez-hardware.conf:76-91; backend-native.c:902-960; hci.c:34-65; linux net/bluetooth/sco.c:963-994; hci_sync.c:4802-4820; btusb.c:2385-2404
- [proven-by-source/high] 'hci0: corrupted SCO packet' is printed when btusb cannot reassemble an SCO packet from USB isochronous frames: either the length exceeds the buffer or the handle is unknown, which happens with lost isoc fragments or SCO data racing link setup/teardown. It is a host/USB-side symptom, not a radio metric. Sporadic occurrences at switches are expected; continuous ones point to a codec/alt-setting problem.
  EVIDENCE: btusb.c:1341-1367 (comment: 'USB isochronous transfers are not designed to be reliable and may lose fragments'), 1399-1412 (-EILSEQ), 1660-1667 (log)
- [proven-by-source/medium] 'Unable to disable scanning: -16' is LE Set Scan Enable(disable) failing with HCI status 0x0C Command Disallowed (mapped to EBUSY) in the passive/active scan paths. It shows that discovery or an LE scan was being (re)started, for example by minitoo-autoaddr's 12 s 'scan on' (run when it thinks the device is disconnected) or another client. Discovery competes for airtime with A2DP/eSCO/ACL.
  EVIDENCE: linux hci_sync.c:2232-2250, 6005-6015; lib.c:106-107; scripts/minitoo-autoaddr.py:59-65, 75-96
- [proven-by-source/high] The RSSI values (-58..-85) in bluetoothctl info come only from discovery results (BR/EDR inquiry or LE advertising) and are cleared after discovery. They do not measure the connected link. Use HCI Read RSSI / Link Quality / AFH map instead; an unprivileged raw HCI socket is allowed to send these.
  EVIDENCE: bluez 5.85 src/adapter.c:7302, 7445-7447, 1711-1716; linux net/bluetooth/hci_sock.c:140-159 (OGF_STATUS_PARAM mask 0xea = OCF 0x01,0x03,0x05,0x06,0x07)
- [proven-by-source/high] HFP over the native backend is invisible on BlueZ D-Bus: PipeWire opens SCO sockets itself, and MediaTransport1 covers only A2DP here. HCIGETCONNLIST is an unprivileged ioctl that lists SCO/eSCO/ACL links and our role. That makes it the best root-free, cheap, authoritative SCO signal; pw-dump -m is a heavier alternative for the profile.
  EVIDENCE: backend-native.c:2593-2690; linux hci_sock.c:1052-1075, 1124-1125 (no capable() for HCIGETCONNLIST, unlike HCIDEVUP 1128); include/net/bluetooth/hci_sock.h:75, 131-138; hci.h:560-562, 674; pipewire src/tools/pw-dump.c:1562, 1581
- [proven-by-source/high] USB autosuspend does not persist because btusb calls usb_enable_autosuspend() at every probe or re-enumeration when enable_autosuspend is set; its default comes from CONFIG_BT_HCIBTUSB_AUTOSUSPEND. A manual 'echo on > power/control' is undone by the next probe, and a udev 'add' rule on the device can run before btusb binds.
  EVIDENCE: linux btusb.c:35, 4403-4404, 4666-4667; https://android-kvm.googlesource.com/linux/+/eff2d68ca7388ee1c08811c6bbf4d8587cba01da%5E%21 (Kconfig rationale)
- [proven-by-source/high] The HANDOFF over-decodes BRSF=671. It is bits 0,1,2,3,4,7,9 = EC/NR, 3-way, CLI, voice recognition, remote volume, codec negotiation, eSCO S4. It does NOT include enhanced call status/control or HF indicators.
  EVIDENCE: pipewire spa/plugins/bluez5/defs.h:266-276; 671 = 0b1010011111
- [documented-by-third-party/medium] A third-party source documents a USB mode. Plugged into USB, the MiniToo enumerates as Jieli VID 4C4A / PID 4E55 with Mass Storage, USB Audio speaker plus microphone ('Divoom Audio') and HID Consumer Control (volume/play). The SPP pixel protocol does not run over USB, and Bluetooth RFCOMM still drives the screen. The same author warns that 'A2DP shares this radio and will stall the screen'.
  EVIDENCE: ext/divoom-minitoo-tools/usb_minitoo.py:1-11, 84-85; screen-play.py:10-11, 679-689; FINDINGS.md:85 (SPP_CHANGE_MODE incl. UAC)
- [proven-by-source/high] The dead indicator code costs about 50 failed open() calls per second. present() runs on every service-loop iteration (poll timeout 20 ms), and each call opens ~/.cache/minitoo-indicator.
  EVIDENCE: display.py:55-66, 69; sdk python/hermes_gadget/linux/control.py:60, 137-139
- [documented-by-third-party/low] Wi-Fi/BT coexistence on the 7265/3165: iwlwifi bt_coex_active defaults to true and the firmware arbitrates the shared antenna. If this card's Wi-Fi is used on 2.4 GHz it takes airtime from BT ACL/eSCO. Setting bt_coex_active=0 removes the arbitration and is not a BT fix.
  EVIDENCE: linux v7.0 drivers/net/wireless/intel/iwlwifi/iwl-drv.c:2008, 2128-2143; https://wiki.debian.org/iwlwifi

## SOLUTIONS
- (5.2) [implement-opt-in] hw_test=True
  APPROACH: (a) Opt-in HFP gate: pause display uploads during the switch window, keep the DLC idle instead of letting ready timeouts close it, and send only the latest frame afterwards.
  IMPL: Prototype (tests pass, not hardware-tested): scratchpad/proto/src/hermes_minitoo/linkstate.py, display.py, runtime.py, config.py; full diff in scratchpad/proto/mitigations.diff.

Config keys under minitoo (all absent by default): hfp_gate=true, hfp_gate_settle_ms=1500, hfp_gate_post_mic_ms=3000, hfp_gate_max_s=60, hci_dev=0.

Signals:
(1) Leading signal: runtime.install_audio_hooks() wraps hermes_gadget.linux.audio.Audio.mic_start and mic_stop so the display learns when the Gadget really opens or closes the mic. That event is what makes WirePlumber switch 500 ms later.
(2) Kernel truth: HCIGETCONNLIST ioctl 0x800448D4 on a raw HCI socket, no root, with a ctypes fallback when Python lacks AF_BLUETOOTH. It reports whether an SCO_LINK(0) or ESCO_LINK(2) to minitoo.address exists.

Gate logic:
```
blocked = mic_open
       or (sco_seen and now - last_sco < settle)
       or (now - mic_closed_at < post_mic_hold)
```
With a max_s safety valve.

Worker:
```
rgb = take_latest()
while gate.blocked():
    cond.wait(0.25)
    if newer frame:
        re-pick (latest wins)
send_rgb888(rgb)
```
In the retry loop, if gate.risky(), park the frame back into _pending instead of sleeping and retrying.

The log becomes 'display paused: HFP/SCO switch' and then 'resumed'. There are no 8 s waits, no close and no backoff. Thinking or Speaking lands on the first attempt about 3.5-5 s after release.

After running the steady-HFP diagnostic, an optional 'transitions-only' mode (block only around SCO up/down) can be added if the device answers during steady HFP.
  RISK: None: it sends less traffic. If the probe fails the gate falls back to mic-based timing, and max_s bounds any stall.
- (5.2) [implement-opt-in] hw_test=True
  APPROACH: (b) Shorter ready timeout and bounded chunk-send timeout, applied only while switching.
  IMPL: transport.send_rgb888(rgb, *, ready_timeout=None, chunk_timeout=None). None keeps the current behaviour.
- _wait_for_ready(timeout) uses the override.
- After the ACK, sock.settimeout(chunk_timeout). Today each chunk's sendall inherits the leftover of the 8 s ready deadline (transport.py:126), so a credit stall can block up to about 8 s per chunk.

Display keys: switch_ready_timeout_ms (e.g. 1500) and chunk_timeout_ms (e.g. 2000). The short ready timeout is used when gate.risky(), or without a gate when screen=='listening' or within 5 s of leaving it (MiniTooDisplay._switching()).

First add ACK-latency logging. In _wait_for_ready, record t0 before the loop and on success log `LOG.debug('ready ACK in %.0f ms', ...)`, at INFO when above 500 ms. Then pick the timeout from measured normal-mode latency × 3.

Also consider using min(connect_timeout, switch_ready_timeout) for connect() during switching.
  RISK: None. Too short a value only causes extra retries during the window; the normal-mode default stays at 8000 ms.
- (5.2) [implement-opt-in] hw_test=True
  APPROACH: (c) listen_preroll: get the Gadget's own Listening frame onto the screen before SCO, by deferring the real mic open until that frame has been uploaded (or a cap expires). The literal variant (send a frame, then press) does not work because the Gadget renders Listening only after the press, and the press opens the mic synchronously.
  IMPL: Keys: minitoo.listen_preroll=true, listen_preroll_max_ms=1500. Default off: the user said 'оставь как есть' (leave it as is).

Runtime hooks (prototype runtime.py):
```
Audio.mic_start(rate) -> orig_stop(); disp.arm_listening()
    self._minitoo_deferred = (rate, now + max)
    return True                        # the core switches to Listening and renders it
Audio.read() -> if deferred and (disp.listening_sent.is_set() or now >= deadline):
    orig_start(rate); disp.note_mic(True)
```
Display: present() bypasses the 2.5 s throttle when the screen changes to 'listening'. The worker sets listening_sent after send_rgb888 returns for a frame captured with screen=='listening'. A 100-200 ms settle could be added so the speaker can render before the switch.

Talk-key (opt-in env MINITOO_PREROLL=1), after button('press'):
```
t0 = time.time()
while time.time() - t0 < 4 and not sco_up(MAC):
    time.sleep(0.05)
```
Only then pw-play 'Говорите' and pw-record. Otherwise the deferred switch would cut the prompt mid-A2DP.

Expected cost: the mic opens about 0.2-0.5 s later typically (14-31 chunks), at most 1.5 s. The SCO handshake (about 0.5 + 0.3-0.7 s) then follows as today.
  RISK: None to the device. Functional risks: the Hermes server sees audio.start up to 1.5 s before the first PCM, and if the speaker drops the frame while entering call mode the screen is unchanged (same as today).
- (5.2) [implement-opt-in] hw_test=True
  APPROACH: (d1) screen_change_immediate: queue a frame immediately when device.screen() changes, instead of waiting out update_interval_ms.
  IMPL: In present():
```
screen = device.screen()
changed = screen != self._last_screen
immediate = changed and self.screen_change_immediate
if not self.dirty or (now - last_queued < interval and not immediate):
    return
```
Key: minitoo.screen_change_immediate=true. This removes the mean 1.36 s (max 2.5 s) delay for Listening, Thinking, Responding and Ready. Without preroll it only makes Listening race the 500 ms switch; it does not guarantee it.
  RISK: Slightly more back-to-back uploads on rapid screen changes. Uploads stay serialized and latest-wins, so the 0x8B rules ('never overlap uploads') still hold.
- (5.2) [implement-now-safe] hw_test=False
  APPROACH: (d2) Bug fix: allow retry_window_s and the new keys through config validation.
  IMPL: config.py: extend _MINITOO_ALLOWED with retry_window_s, hfp_gate*, hci_dev, switch_ready_timeout_ms, chunk_timeout_ms, screen_change_immediate, listen_preroll, listen_preroll_max_ms.
- Validate the optional integers only when the key is present, e.g. retry_window_s 1..3600, switch_ready_timeout_ms 100..30000.
- Validate the booleans with isinstance(bool).
See prototype config.py. Absent keys mean exactly today's behaviour.
  RISK: None.
- (5.2) [implement-now-safe] hw_test=False
  APPROACH: (d3) Instrumentation (logging only) to separate app silence, DLC drop and ACL loss.
  IMPL: 1. Log the exception type and errno in the retry line (already %r; keep it).
2. Log ready-ACK latency.
3. In verbose mode, log every unsolicited packet parsed in _wait_for_ready (command, first 6 argument bytes), e.g. `8B 55 01 <idx>`, to learn whether these are resend requests.
4. On each failure, log sco_up(addr) and the role from linkstate.connections(), and whether the ACL still exists.
5. Remove the 50 Hz indicator file polling (display.py:55-81) or put it behind a flag (HANDOFF §8.1).
  RISK: None.
- (5.2) [diagnostic-first] hw_test=True
  APPROACH: (d4) A/B test without PipeWire's dummy call indicator (+CIEV call=1), which may push the MiniToo into a call mode that stalls SPP and remaps buttons (also relevant to 5.1).
  IMPL: Owner, no root. Create ~/.config/wireplumber/wireplumber.conf.d/52-minitoo-no-dummy-call.conf:
```
monitor.bluez.rules = [
  { matches = [ { device.name = "~bluez_card.B1_21_81_.*" } ]
    actions = { update-props = { bluez5.disable-dummy-call = true } } }
]
```
Then `systemctl --user restart wireplumber` and reconnect the speaker. The property is read from device props at bluez5-device.c:3380-3381, the same path as bluez5.auto-connect/hw-volume in the upstream example rule.

Compare one press: display retries, button events in phase=recording, and whether SCO survives the whole recording. Revert by deleting the file.
  RISK: No device risk. Functional risk: per the PipeWire docs, some headsets drop SCO after a timeout when no call is active, so the recording could stop early.
- (5.7) [implement-now-safe] hw_test=False
  APPROACH: Make USB autosuspend off persistent for the btusb adapter (needs root; the owner installs these files).
  IMPL: Fix 1 (primary). Create /etc/modprobe.d/btusb-no-autosuspend.conf:
```
# btusb re-enables USB autosuspend at every probe when this is Y (drivers/bluetooth/btusb.c)
options btusb enable_autosuspend=0
```
Then `sudo update-initramfs -u` and reboot. Alternatively add btusb.enable_autosuspend=0 to GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub and run `sudo update-grub`.

Fix 2 (belt and braces). Create /etc/udev/rules.d/91-bt-intel-0a2a-no-autosuspend.rules:
```
ACTION=="bind", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface", DRIVER=="btusb", ATTRS{idVendor}=="8087", ATTRS{idProduct}=="0a2a", RUN+="/usr/bin/sh -c 'echo on > /sys%p/../power/control'"
ACTION=="add|change", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="8087", ATTR{idProduct}=="0a2a", TEST=="power/control", ATTR{power/control}="on"
```
Then `sudo udevadm control --reload-rules && sudo udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=8087`.

The first rule runs after btusb has bound (after its usb_enable_autosuspend call). The second covers coldplug.

If TLP is installed, set USB_EXCLUDE_BTUSB=1 in /etc/tlp.conf. Do not run 'powertop --auto-tune'.

Verify with: `cat /sys/module/btusb/parameters/enable_autosuspend` (expect N) and the power/control loop in the diagnostics (expect on).
  RISK: None. It costs about 1-2 W less power saving on the BT USB function.
- (5.7) [diagnostic-first] hw_test=True
  APPROACH: Check which HFP codec is in use and, if it is mSBC on this non-WBS Intel, force CVSD as an experiment. This targets 'corrupted SCO packet', the garbage at SCO start (HANDOFF §8.3) and unstable SCO.
  IMPL: First check during a recording: `pw-dump | grep -oE '"api.bluez5.(codec|profile)": "[^"]+"' | sort | uniq -c`.

If the codec is msbc, create ~/.config/wireplumber/wireplumber.conf.d/51-minitoo-cvsd.conf:
```
monitor.bluez.properties = { bluez5.enable-msbc = false }
```
force_msbc=0 clears the MSBC, MSBC_ALT1 and MSBC_ALT1_RTL features (quirks.c:227, 370-374), so device_supports_codec(MSBC) returns false and CVSD/S3 is used. Then `systemctl --user restart wireplumber`.

Compare the 'corrupted SCO packet' count per recording and the first-second RMS in the talk-key VAD log.
  RISK: None. Narrowband 8 kHz audio may slightly reduce STT quality. Revert by deleting the file.
- (5.7) [implement-opt-in] hw_test=True
  APPROACH: Stop discovery from competing with live links: minitoo-autoaddr must never start 'scan on' while an ACL or SCO link to any B1:21:81 device exists, and only after repeated misses.
  IMPL: Opt-in env MINITOO_AUTOADDR_SAFE_SCAN=1. In cycle():
```
if info(cur).Connected == 'yes':
    misses = 0
    return
if SAFE:
    if any(a.startswith(PREFIX) for a, t, _ in linkstate.connections(0)):
        misses = 0
        return
    misses += 1
    if misses < 3:
        return   # about 60 s of consistent absence
# existing scan/pair path
```
Also skip 'pair' if Paired==yes. This also removes one source of 'Unable to disable scanning: -16'.
  RISK: None. Detection of a genuine address change is delayed by about 40 s.
- (5.7) [diagnostic-first] hw_test=True
  APPROACH: Reduce RF and coexistence load on the Intel 7265/3165 combo card.
  IMPL: 1. If the server is on Ethernet and this card's Wi-Fi is unused: `nmcli radio wifi off`, or rfkill the wifi device only. That frees the shared antenna and coexistence arbitration for BT.
2. If Wi-Fi is needed, keep it on 5 GHz. Do not set iwlwifi bt_coex_active=0: that removes arbitration and BT loses.
3. Keep USB 3.0 devices and cables (for example external disks) away from the PC's antenna side and ports. USB 3 radiates in the 2.4 GHz band.
4. Measure link quality with HCI Read RSSI/LQ/AFH (unprivileged), not bluetoothctl RSSI.
  RISK: None.
- (5.7) [diagnostic-first] hw_test=True
  APPROACH: Replace the 2014-era internal Intel Stone Peak BT with an external USB adapter, if the diagnostics implicate the host side (controller, USB isoc, RF).
  IMPL: Sensible class: a Realtek RTL8761B-family BT 5.x USB dongle. The kernel flags it WIDEBAND_SPEECH (btusb.c:793-796, ids 2550:8761 and 0bda:8771), firmware is rtl_bt/rtl8761b*_fw.bin from linux-firmware, and the PipeWire HW DB has a dedicated 0bda rule (bluez-hardware.conf:83-84). Prefer a model with an external antenna on a USB 2.0 extension cable, placed in line of sight and away from USB 3 devices.

Avoid unbranded 'CSR8510' clones: the kernel carries lock-up workarounds for them (btusb.c:2540-2560).

To disable the internal adapter, add /etc/udev/rules.d/90-disable-intel-bt.rules:
```
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="8087", ATTR{idProduct}=="0a2a", ATTR{authorized}="0"
```
Then re-pair the MiniToo on the new adapter; link keys are per adapter. Broadcom BCM20702 dongles are an older second choice.

This fixes host, controller and RF causes only. It does not fix a MiniToo-firmware SPP stall, so run the steady-HFP diagnostic first.
  RISK: None to the MiniToo. Requires re-pairing; the autoaddr/config address logic must follow the new adapter.
- (5.2/5.7 (and 5.1)) [diagnostic-first] hw_test=True
  APPROACH: Topology change: carry audio over the MiniToo's USB audio function (speaker, mic and HID keys) and keep Bluetooth only for the SPP display. The BT profile never switches, so there is no SCO and no A2DP load.
  IMPL: 1. Plug the MiniToo into the server with USB-C and check `lsusb -d 4c4a:4e55 -v`, `arecord -l`, `aplay -l`, `wpctl status` and /proc/bus/input/devices for 'Divoom Audio'.
2. If the microphone and Consumer Control exist on Linux, point config.json audio.input and audio.output at the USB ALSA/PipeWire nodes.
3. Extend talk-key find_event() to also accept the USB HID device (KEY_PLAYPAUSE).
4. Set the Bluetooth card profile to 'off' (`wpctl set-profile <bluez_card id> 0`) or exclude a2dp/hfp for this device, so only ACL plus RFCOMM SPP remain.
5. Verify that SPP 0x8B still works while the speaker is in UAC mode. Third-party Windows use says it does.
Requires the speaker to be within cable reach of the server.
  RISK: Low: a standard USB connection. Unknown whether the firmware drops BT SPP when its audio source mode is UAC; test it.

## UNSOLVABLE
- (Showing 'Listening' through the 0x8B live path while the A2DP->HFP/SCO switch is in progress (host-side only, as the code works today).) Timeline:
1. The SDK opens the mic synchronously inside the press (app.cpp:828), and WirePlumber starts the switch 500 ms later (lua:35). SCO is up about 0.6-1.5 s after the press.
2. The Listening frame exists after about 0.1 s, but the 2.5 s throttle delays it by 0-2.5 s (mean about 1.36 s). Even unthrottled, it must first wait for any in-flight upload and then send 14-31 chunks typical (241 worst) at 5 ms pacing plus an announce round trip. That is about 0.15-0.4 s typical and 1.5-2.5 s worst.
3. Once the switch starts, the speaker sends no `8B 55 00 01` for at least 8 s (the observed timeouts; f11ae6a waited 8.7 s). Every frame after that has two outcomes: its announce lands during the stall and is ignored, or it is sent after recovery, when Listening is already obsolete.
4. Nothing on the host can make the speaker's app task answer. The 0x8B protocol has no 'preload now, show later' primitive: each announce means a full upload.
Delivering before SCO is possible only by reordering, which makes the switch wait for the frame (listen_preroll). Delivering during the switch is not possible.
  WOULD CHANGE: Any one of these:
(1) The steady-HFP diagnostic shows the speaker ACKs 0x8B within about 1 s during steady HFP, so only the transitions are silent. Then a gate in 'transitions-only' mode shows Listening about 1-2 s late.
(2) A small command such as custom-face Channel/SetClockSelectId (one ~80-byte RFCOMM frame, pre-uploaded face) is proven to be processed during the transition.
(3) Audio moves to USB so no switch happens.
(4) A firmware change.
- (Fixing the MiniToo's SPP stall during HFP in firmware (custom firmware).) 1. No practical delivery route on a stock unit: SPP OTA is blocked by the bootloader-state catch-22, the SD path is closed, the USB DFU topology is unclear (ATS2831 vs JieLi), and JTAG/boot straps require opening the case (research/FIRMWARE.md; HANDOFF §9.3). minitoo-forth ran only in simulation.
2. The SoC is disputed: Jieli per bugzmanov, Actions ATS2831 plus JieLi AC690N per minitoo-forth, and the third-party USB enumeration shows Jieli 4C4A:4E55 'BR28'. The ATS2831 datasheet is under NDA.
3. The behaviour to change (task priorities, memory overlays, or SPP servicing while the call/eSCO audio pipeline is active) lives in the vendor BT stack/RTOS blob without symbols. A 6.5 KiB code cave can add a command handler but cannot re-architect scheduling.
4. There is real brick risk ('more probing would brick the device again', bugzmanov; large transfers rebooted devices, dotnet docs), and no known rollback.
  WOULD CHANGE: A verified non-destructive flashing and recovery route (DFU or JTAG with dump/restore), a confirmed SoC, a symbolized or decompiled vendor SDK for that SoC, or Divoom shipping a firmware update.
- (Guaranteeing an uninterrupted RFCOMM display across HFP by changing only the host adapter/stack, if the stall is inside the speaker.) The source shows the host side does nothing that should stall ACL/RFCOMM during an SCO switch. There is no RFCOMM close, no role switch, only a forced sniff exit and an eSCO setup, and there is ample ACL bandwidth. If the steady-HFP diagnostic confirms the speaker ignores 0x8B while in call mode, no adapter, kernel or PipeWire change can force a remote application to answer. A new adapter only removes host-side contributors: USB isoc errors, autosuspend, Wi-Fi coexistence and antenna or RSSI problems.
  WOULD CHANGE: An A/B run of the same press sequence on a different adapter (for example RTL8761B) that shows ACKs during HFP. That would prove a controller-side cause.

## DIAGNOSTICS
- # D1 Decisive test, no root: hold HFP for 60 s while the Ready screen keeps animating, and see whether 0x8B frames go through during steady HFP or only fail at the transitions. For exact ACK timing, temporarily set "ready_timeout_ms": 30000 in config.json and restart hermes-minitoo before this test.
IN=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/hermes-minitoo-gadget/config.json')))['audio']['input'])"); (timeout 60 pw-record --target="$IN" --rate 16000 --channels 1 --format s16 - > /dev/null &); journalctl --user -u hermes-minitoo -f -o short-precise | grep -E 'retry|recovered|connected|failed'
- # D2 Live link table, no root: ACL/SCO/eSCO links to the speaker and our role, every 0.5 s. Run it during D1 and during one normal press.
python3 - <<'EOF'
import socket,fcntl,struct,time
T={0:'SCO',1:'ACL',2:'eSCO',0x80:'LE'}
while True:
    s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI)
    b=bytearray(struct.pack('<HH',0,8)+bytes(16*8)); fcntl.ioctl(s.fileno(),0x800448D4,b,True); s.close()
    n=struct.unpack_from('<HH',b)[1]; out=[]
    for i in range(n):
        h,a,t,o,st,lm=struct.unpack_from('<H6sBBHI',b,4+16*i)
        out.append('%s %s h=%d %s'%(':'.join('%02X'%x for x in a[::-1]),T.get(t,t),h,'central' if lm&1 else 'peripheral'))
    print(time.strftime('%H:%M:%S'),'; '.join(out) or '-',flush=True); time.sleep(0.5)
EOF
- # D3 Error-type histogram of display failures: app-level silence (TimeoutError) vs DLC/ACL loss (ConnectionError/Errno), and EBUSY on reconnect.
journalctl --user -u hermes-minitoo --since '-2 days' --no-pager | grep -oE "retry [0-9]+: [A-Za-z]+(Error)?\('[^']{0,70}|\[Errno [0-9]+\][^'\"]{0,40}|recovered after [0-9]+ retries \([0-9.]+s\)" | sed -E 's/retry [0-9]+: //' | sort | uniq -c | sort -rn
- # D4 Temporary info-level PipeWire/WirePlumber BT logs for one press; revert afterwards. Look for: Switching profile / Restoring profile / lost RFCOMM / SCO socket error / alt6 / msbc / Transport released.
systemctl --user set-environment WIREPLUMBER_DEBUG='2,s-device:3,spa.bluez5.native:3,spa.bluez5.device:3' && systemctl --user restart wireplumber
journalctl --user -u wireplumber -f -o short-precise | grep -E 'Switching profile|Restoring profile|lost RFCOMM|SCO socket|alt6|msbc|released|+BCS|codec'
systemctl --user unset-environment WIREPLUMBER_DEBUG && systemctl --user restart wireplumber
- # D5 HFP codec actually used: run during a recording (msbc on 8087:0a2a would be suspicious).
pw-dump | grep -oE '"api.bluez5.(codec|profile)": "[^"]+"' | sort | uniq -c
- # D6 Kernel side: Intel firmware patch status ('completed and deactivated' means the patch was NOT applied), corrupted SCO per boot, tx timeouts, scan errors.
journalctl -k -b --no-pager | grep -iE 'Intel (Bluetooth firmware file|BT fw patch|firmware patch)|failed to open.*fw|corrupted SCO|tx timeout|disable scanning|hci0' | tail -60; journalctl -k -b --no-pager | grep -c 'corrupted SCO packet'
- # D7 Autosuspend state: Kconfig default, btusb parameter, and current USB power control for 8087:0a2a.
grep BT_HCIBTUSB_AUTOSUSPEND /boot/config-$(uname -r); cat /sys/module/btusb/parameters/enable_autosuspend; for d in /sys/bus/usb/devices/*; do [ -f $d/idVendor ] && [ "$(cat $d/idVendor):$(cat $d/idProduct)" = 8087:0a2a ] && echo "$d control=$(cat $d/power/control) status=$(cat $d/power/runtime_status) delay_ms=$(cat $d/power/autosuspend_delay_ms)"; done
- # D8 Wi-Fi coexistence and USB 3 noise sources on the same box.
iw dev; for i in $(ls /sys/class/net | grep -E '^wl'); do iw dev $i link; done; cat /sys/module/iwlwifi/parameters/bt_coex_active 2>/dev/null; rfkill list; lsusb -t | grep -E '5000M|10000M'
- # D9 Correlate 'Unable to disable scanning' with minitoo-autoaddr discovery runs.
journalctl -k -b -o short-iso --no-pager | grep 'disable scanning'; journalctl --user -u minitoo-autoaddr -b -o short-iso --no-pager | grep -E 'эфире|пробую|сопряжено|не удалось|config:'
- # D10 Real link metrics instead of discovery RSSI. Allowed for unprivileged HCI sockets; works if hcitool is installed. For connected BR/EDR links RSSI is relative to the golden receive range (0 = fine).
MAC=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/hermes-minitoo-gadget/config.json')))['minitoo']['address'])"); for i in $(seq 30); do hcitool rssi $MAC; hcitool lq $MAC; sleep 1; done; hcitool afh $MAC
- # D11 Owner-run btmon capture of one press (needs root; do not route sudo through the bot). Then grep the decode.
sudo btmon -w ~/minitoo-switch.btsnoop   # press Play/Pause once, wait until Ready returns, then Ctrl-C
btmon -r ~/minitoo-switch.btsnoop | grep -nE 'Enhanced Setup Synchronous|Synchronous Connect Complete|Air mode|Transmission interval|Retransmission window|Mode Change|Role Change|Disconnect|Reason|RFCOMM|8b 00|04 8b 55' | head -200
- # D12 Does the MiniToo expose a USB microphone and HID keys on Linux (topology fix)? Plug in USB-C first.
lsusb -d 4c4a:4e55 -v 2>/dev/null | grep -E 'iProduct|bInterfaceClass|bInterfaceSubClass|iInterface'; arecord -l; aplay -l; grep -B2 -A6 -i 'divoom' /proc/bus/input/devices; wpctl status | grep -i -E 'divoom|minitoo'

## VERIFIER
- [corrected] The 'did not send the 0x8B ready ACK' log line means the DLC stayed open and the MiniToo app layer stayed silent; a real DLC drop would show up as EOF/ConnectionError or OSError.
  NOTE: The exception semantics are right. TimeoutError is raised only at transport.py:124-125 and 129-130; ConnectionError on EOF at 131-132. A remote DISC makes the kernel set ECONNRESET, so Python raises ConnectionResetError, which is in the same ConnectionError/OSError family. The localisation is too narrow, though. A timeout only proves that no bytes and no close arrived within 8 s. The ACL supervision timeout is about 20 s, so an L2CAP/ACL stall or withheld RFCOMM credits would look identical: with no credits, our announce can sit in our own TX queue and never reach the device. Correct wording: 'no data and no disconnect for 8 s', with the speaker as the most likely place for the stall.
- [confirmed] Part of the outage is self-inflicted: on any exception we close our own DLC, then a 2 s backoff applies, then a fresh connect with timeout max(1, ready_timeout) = 8 s.
  NOTE: Close on exception: transport.py:159-162. Backoff check: 99-101. Connect timeout: 103. Also confirmed: after the ACK, each chunk's sendall inherits the leftover of the ready deadline, because settimeout(remaining) at transport.py:126 is never reset.
- [corrected] Our SPP DLC and PipeWire's HFP DLC share one kernel rfcomm_session; closing the session closes every DLC; working recordings prove routine switches don't kill it; PipeWire's 'lost RFCOMM connection' marks the rarer total-loss events of 5.7.
  NOTE: Session reuse is confirmed: rfcomm_session_get(src,dst) at rfcomm/core.c:384-389 and 730-741, and rfcomm_session_close closes all DLCs at 743-756. The inference that working recordings prove the session survived is sound. But 'lost RFCOMM connection.' (backend-native.c:2549-2552) is only HUP/ERR on PipeWire's own HFP DLC fd. It can also fire when just the HFP DLC is closed by either side, so it does not by itself mean a total link loss.
- [confirmed] Reconnect can fail with EBUSY while the old DLC is still in BT_DISCONN (20 s, or 40 s with queued TX).
  NOTE: The DLCI-exists check returns -EBUSY at rfcomm/core.c:393-396. __rfcomm_dlc_disconn sets the DISC timer, doubled when tx_queue is non-empty, at 437-448. RFCOMM_DISC_TIMEOUT is HZ*20 at rfcomm.h:30. The mechanism is proven; that it happens on this speaker is not. The HANDOFF's 2-7 s recoveries suggest the speaker normally answers DISC with UA.
- [corrected] The talk press opens the mic before mode_=Listening, and WirePlumber switches 500 ms after a capture link appears and restores 2000 ms after the last link goes (hard-coded).
  NOTE: Ordering is confirmed: app.cpp:828 (mic->start) runs before 847 (mode_=Listening); audio.py:73-92. Constants are confirmed: autoswitch lua HEAD:34-35 and ext2/wp-autoswitch-0.5.13.lua / 0.5.14.lua:34-35. Not proven: that the SDK's PortAudio stream is the trigger. Autoswitch only counts nodes with media.class 'Stream/Input/Audio' linked to a bluez5.loopback source (wp-autoswitch-0.5.13.lua:290-335, 402-405). Whether PortAudio's resolution of audio.input='bluez_input.<MAC>' on the server (ALSA-pipewire vs pipewire-jack) produces such a node is unverified. HANDOFF §5.6 (the prompt start is cut right after the press) is circumstantial support.
- [confirmed] The 2.5 s throttle delays the Listening frame by 0-2.5 s (mean ≈1.36 s), almost always after the 500 ms trigger.
  NOTE: Throttle: display.py:82. Ready-mode redraws happen only on the blink, frame%45<2 (ui.cpp:84-99), so queue gaps alternate 2.5 s and 2.0 s. Mean = (2.5·1.25 + 2.0·1.5)/4.5 = 1.36 s, and P(delay < 0.5 s) ≈ 11%. This holds only in hero-mode Ready. While a reply is shown (showing_reply, app.cpp:1495-1497, kReplyLingerMs = 20 s), the screen can be static and a press may not be throttled at all.
- [confirmed] Retry-storm mechanics: an animated screen queues a frame every 2.5 s, one blocking cycle is about 10-11 s, and the logged 'recovered after N retries' is measured from the newer frame.
  NOTE: display.py:99-132; started timestamp at 108; newer-frame break at 129-131; 1 s sleep at 132. Listening, Thinking and Responding are hero screens with per-100 ms animation keys: ui.cpp:91-99, app.cpp:29, 1329-1331, 1440-1441.
- [confirmed] retry_window_s cannot be configured: load_config rejects it and the service fails to start.
  NOTE: config.py:10-13 whitelist, 27-28 rejection. load_config is called on the startup path at runtime.py:23.
- [corrected] Commit f11ae6a could not have worked by design.
  NOTE: Too absolute. It was very unlikely to work, not impossible: in about 11% of presses the throttle delay is under 0.5 s, enough to beat the switch. Also missed by the report: f11ae6a wrote ACK_FILE after send_rgb888 of any frame (git show f11ae6a, display.py hunk), so a stale Ready frame could set it. Even 'True' would not have proven that Listening was delivered.
- [confirmed] `8B 55 01 <idx>` may be a resend request that our transport ignores.
  NOTE: Correctly labelled as a low-confidence hypothesis. Evidence against it: the cited resend handler (DivoomClient.cs:262-266, 494-503) is for the 0xBE custom-face pull protocol. dotnet's own live 0x8B path (DivoomClient.cs:181-187) also ignores 8B 55 01 and is documented as rendering correctly. FINDINGS.md:1065 calls these packets ACKs.
- [confirmed] PipeWire 1.6.2's sequence on a2dp-sink -> headset-head-unit: release the A2DP transport (BlueZ sends AVDTP SUSPEND only if STREAMING), ensure the HFP codec, emit the SCO nodes, non-blocking SCO connect, forced exit from sniff, then eSCO setup.
  NOTE: bluez5-device.c:1431-1530 and 1364-1376; a2dp.c:3381-3420; backend-native.c:2593-2690; hci_conn.c:1789-1797. One nuance: the kernel uses Enhanced Setup Synchronous Connection only if the controller supports it, otherwise the legacy command (hci_conn.c:459-480).
- [confirmed] Kernel eSCO parameter sets: CVSD S3/S2/S1/D1/D0, mSBC T2/T1, never S4.
  NOTE: hci_conn.c:52-68 (v7.0).
- [confirmed] PipeWire sends a dummy '+CIEV: 2,1' on SCO acquire and '+CIEV: 2,0' on release unless bluez5.disable-dummy-call is set.
  NOTE: backend-native.c:1067-1081, 2808-2810, 2863-2865. Two additions: it is sent only if the HF enabled indicator events (cind_call_notify), and it is sent when the SCO connect starts, before SCO is up. The flag is read from device info at bluez5-device.c:3380-3381 and from the Props param at 3109-3112. The docs file it under 'Monitor properties' (pipewire-props.7.md:1100-1150), but per the code a device-level rule works.
- [unverifiable] ACL capacity during eSCO is ample, so bandwidth cannot explain the stall.
  NOTE: Spec arithmetic only; I did not check it against the Core Spec tables. It is plausible for air time. A third party reports that on this device even A2DP 'shares this radio and will stall the screen' (divoom-minitoo-tools/screen-play.py:10-11, 686-689). That points to device-side scheduling or CPU as the bottleneck, not air bandwidth, which is consistent with the report's firmware hypothesis.
- [corrected] Upstream measured 259-416 ms per 14-15-chunk upload.
  NOTE: The doc (divoom-minitoo-dotnet/docs/live-streaming-without-spinner-or-checkerboard.md:258-260) says 259-341 ms for 14-15 chunks, and 416 ms in a separate 'representative app run'. The local LZO benchmark numbers were not re-run.
- [confirmed] 8087:0a2a is the Bluetooth part of an Intel 7265/3165 Stone Peak combo card; Linux flags it COMBINED | NO_WBS_SUPPORT | BROKEN_SHUTDOWN_LED; among legacy ROM parts only SdP supports WBS.
  NOTE: btusb.c:492-494; btintel.c:3467-3486 (hw_variant 0x08 StP). Web: a linux-firmware commit names 7265 = stonePeak (https://kernel.googlesource.com/pub/scm/linux/kernel/git/afaerber/linux-firmware/+/897330f38d2db8986fc8965709b1852ba76896a7); the 0a2a support patch is at https://lkml.iu.edu/hypermail/linux/kernel/1405.0/02065.html.
- [confirmed] PipeWire can still pick mSBC on this non-WBS adapter.
  NOTE: bluez-hardware.conf:76-91 has no Intel rule. device_supports_codec (backend-native.c:902-960) uses quirks plus LMP_TRSP_SCO/LMP_ESCO (hci.c:34-65). The kernel's NO_WBS flag only drops HCI_QUIRK_WIDEBAND_SPEECH_SUPPORTED, which the kernel uses only for MGMT and erroneous-data reporting (hci_sync.c:4802-4820). sco.c:963-994 accepts BT_VOICE_TRANSPARENT. btusb falls back to alt 1 at 2386-2404. bluez5.enable-msbc=false clears all mSBC features (quirks.c:227, 370-374).
- [confirmed] 'corrupted SCO packet' is a host-side USB isochronous reassembly error.
  NOTE: btusb.c:1341-1412 (handle validation, -EILSEQ) and 1660-1667 (log line).
- [corrected] 'Unable to disable scanning: -16' means Command Disallowed while discovery or an LE scan is starting; correlate it with minitoo-autoaddr's scans.
  NOTE: -16 = EBUSY = HCI status 0x0C (lib.c:106-107) is correct. But hci_scan_disable_sync (hci_sync.c:2232-2250) has many callers: passive-scan updates (3060, 3221), stop discovery (5531, 5537), active scan (6011), pause (6217), LE create connection (6641) and PA sync (7141). The message means the controller refused to stop an LE scan the kernel believed was running, not only that discovery was starting. Also, minitoo-autoaddr only scans when the configured address is NOT Connected (minitoo-autoaddr.py:75-78), so it never competes with a live MiniToo link except during reconnects.
- [confirmed] The bluetoothctl RSSI comes only from discovery and is cleared afterwards; HCI Read RSSI, Link Quality and AFH map are allowed on unprivileged raw HCI sockets.
  NOTE: Discovery-only RSSI: adapter.c:7445-7447. Invalidation in discovery_cleanup: 1711-1716 and 1731-1732. Security filter OGF_STATUS_PARAM 0xea: hci_sock.c:155-156.
- [confirmed] HCIGETCONNLIST is unprivileged and works as a root-free SCO signal.
  NOTE: HCIGETCONNLIST has no capable() check (hci_sock.c:1124-1125), unlike HCIDEVUP (1127-1129). Side effect: every new raw HCI socket that issues an ioctl emits a monitor ctrl-open event (hci_sock.c:1088-1110). Probing every 0.25 s with a fresh socket therefore spams btmon traces.
- [confirmed] btusb re-enables USB autosuspend on every probe, so a manual power/control=on does not persist.
  NOTE: btusb.c:35 (Kconfig default), 4403-4404 (usb_enable_autosuspend in probe), 4666-4667 (module param). The 'bind' uevent is sent after probe returns, so the proposed ACTION=="bind" rule runs after usb_enable_autosuspend.
- [confirmed] BRSF=671 decodes to bits 0,1,2,3,4,7,9; the HANDOFF over-decodes it.
  NOTE: defs.h:266-276. Bits 5 (enhanced call status), 6 (enhanced call control) and 8 (HF indicators) are not set.
- [confirmed] Over USB the MiniToo is a Jieli 4C4A:4E55 composite device (UAC speaker and mic, HID consumer keys); SPP still drives the screen.
  NOTE: Third-party documentation only: divoom-minitoo-tools/usb_minitoo.py:1-11, observed on Windows. The mass-storage string 'BR28 UDISK' identifies a Jieli BR28 = AC701N/JL701N (ext2/jielie/chips/index.md:43, 122; jl-uboot-tool/README.md:79). The report did not draw this conclusion.
- [confirmed] The dead indicator code causes about 50 failed open() calls per second.
  NOTE: present() is called every client.step() (sdk client.py:198-199). The loop uses poll(timeout=0.02) (control.py:60, 137-139). _indicator() opens the file on every call (display.py:55-69). The exact rate depends on how long step() blocks.
- [confirmed] iwlwifi bt_coex_active defaults to true.
  NOTE: iwl-drv.c:2008 and 2128-2143 (v7.0).
- [confirmed] The prototype suite passes: 27 tests (21 original + 6 new), defaults unchanged.
  NOTE: I ran `PYTHONPATH=src pytest -q` in scratchpad/proto: 27 passed. Diffing proto against main for transport.py and config.py shows that absent keys and None overrides keep today's behaviour. Without PYTHONPATH, collection fails in both main and proto.
- [corrected] A custom-firmware fix is not feasible: no flashing route, disputed SoC, a vendor BT stack without symbols or datasheet, and no rollback.
  NOTE: Overstated. If the MiniToo's USB/BT chip is the BR28 named in the USB descriptor, then:
- Its ROM has a USB download (UBOOT) mode, entered by a USB_KEY signal at boot, and it is entered automatically when flash is invalid (jl-uboot-tool/docs/how-to-enter-uboot.md). That gives a dump-before-write and recovery path.
- jl-uboot-tool lists BR28 as 'seems to work' (README.md:79).
- A public AC701N/JL701N SoundBox SDK exists locally (ext2/ac701n_soundbox/sdk; cpu/br28/liba/btstack.a, which has symbol names such as rfcomm_channel_*; tools/sdk.elf; rom.lst).
What holds: the SoC is unconfirmed (ATS2831 per minitoo-forth vs Jieli per bugzmanov and the BR28 USB string), the Divoom app is closed source, brick risk is real, and no hardware dump exists. Correct verdict: not proven infeasible, but very high effort and risk, with a concrete first step (confirm the SoC, then do a read-only flash dump).

### solution concerns
- (c) listen_preroll:
- It signals 'Listening sent' when send_rgb888 returns, which only means the chunks are in the kernel socket, not delivered or rendered. It relies on WirePlumber's 500 ms delay as the margin, so add the 100-200 ms settle.
- It monkeypatches the private hermes_gadget 0.2.0 methods Audio.mic_start/read/mic_stop, which breaks silently on an SDK upgrade.
- It is wrong that the literal variant ('send a frame, then press') cannot work. A host-rendered static Listening image sent and ACKed before the press would show before SCO, without touching the SDK.
- Untested alternative: turn WirePlumber autoswitch off at runtime (`wpctl settings bluetooth.autoswitch-to-headset-profile false`; the setting is subscribed at lua HEAD:653) and let talk-key call `wpctl set-profile` itself after the frame is ACKed, and again after release. First check that the bluez_input loopback node still exists with autoswitch off.
- (d4) disable-dummy-call: the Jieli AC701N reference SDK forces a switch to the BT task on BT_STATUS_SCO_STATUS_CHANGE alone (ret=1) as well as on PHONE_ACTIVE (ret=2) (ac701n_soundbox/sdk/apps/soundbox/task_manager/bt/bt.c:991-1005, 1091-1105). If the MiniToo firmware works like this SDK, removing +CIEV may not change the SPP stall. Restarting WirePlumber tears down and re-registers every BT audio profile; the device Props param (bluez5-device.c:3109-3112) can be changed at runtime instead.
- D1, the 'decisive' test, is not decisive with the current code. A successful upload without retries logs nothing (only 'connected' and 'recovered' are logged), so 'no retry lines' cannot be told apart from 'worker blocked in a 30 s wait'. Ship the d3 ACK-latency logging first. With ready_timeout_ms=30000 the RFCOMM connect timeout also becomes 30 s (transport.py:103).
- D2 calls socket.AF_BLUETOOTH and BTPROTO_HCI directly. The project carries a ctypes fallback precisely because some Python builds lack AF_BLUETOOTH (transport.py:16-35, commit c435c4e), so D2 may raise AttributeError. Use the numeric family 31, SOCK_RAW and protocol 1 instead.
- D4 restarts WirePlumber, which drops and re-establishes the MiniToo audio profiles; the HANDOFF already reports instability with debug logging on. `wpctl set-log-level I` changes the level at runtime without a restart (wireplumber docs/rst/daemon/logging.rst).
- HfpGate:
- The sco_up() probe opens a new raw HCI socket every 0.25 s, which pollutes btmon (D11) with RAW Open/Close events. Keep one socket.
- post_mic_hold 3 s plus the 1.5 s SCO settle may end before the A2DP restart: HANDOFF §5.4 reports about 6.3 s to return to a2dp-sink, and nothing gates the AVDTP START transition.
- With hfp_gate on and listen_preroll off, Listening is never shown by design. That matches today's behaviour but should be stated.
- 5.7 'safe scan' for minitoo-autoaddr: the stated premise (discovery competing with live links) mostly does not apply. cycle() returns immediately when the configured address is Connected (minitoo-autoaddr.py:77-78). The real benefits are fewer 12 s discoveries while the speaker is off, and not running inquiry while the speaker is paging the host to reconnect.
- 'Unable to disable scanning' correlation: as shown above, it can also come from passive-scan, connection and stop-discovery paths, so the absence of an autoaddr scan at that moment does not rule anything out.
- (b) switch_ready_timeout: the 1500 ms example must not be applied before the normal ACK latency has been measured; the report itself says so. With chunk_timeout set, a mid-transfer timeout closes the DLC with chunks still queued. That triggers the 40 s DISC timer path (core.c:437-448) and possible EBUSY on reconnect.
- External-adapter option: with the internal adapter still present the new dongle may become hci1, so the hci_dev setting and the D2/D10 scripts must follow it. Re-pairing also interacts with minitoo-autoaddr's address-replacement logic (HANDOFF §8.9).

### missed
- Concrete firmware-side evidence for the stall hypothesis, which the report leaves abstract. In the Jieli AC701N (BR28) SoundBox SDK, an SCO status change or call event received while a non-BT app task is in front forces app_task_switch_to(APP_BT_TASK) (bt.c:991-1005, 1091-1105). SCO open calls esco_dec_open (with AEC/NR) (bt_event_fun.c:bt_status_sco_change). The switch back runs only 500 ms after the eSCO decoder stops, and re-polls every 500 ms while the phone decoder is running (bt_event_fun.c:152-175). If Divoom's display/SPP handler lives in a non-BT task, this explains both the silence during HFP and the slow recovery. It is applicable only if the MiniToo is JL701N-based (unconfirmed).
- The USB descriptor string 'BR28 UDISK' (divoom-minitoo-tools/usb_minitoo.py:6) identifies a Jieli AC701N/JL701N (jielie/chips/index.md:43, 122), not the AC690N (BR17) that research/FIRMWARE.md:19 names. This resolves part of the 'disputed SoC' question and opens a known ROM USB download path with third-party tooling (jl-uboot-tool) plus a public SDK with symbolized libraries. The custom-firmware analysis should be redone around this.
- HANDOFF symptom 'bluetoothd: getpeername: Transport endpoint is not connected' was not analysed. It comes from BlueZ's SDP server (src/sdpd-request.c:1124-1127): an SDP request arrived on an L2CAP channel whose peer had already gone. The MiniToo, or another device, ran SDP and dropped the channel or link. This marks the unstable reconnect sequences of 5.7, not the display path.
- An option that avoids SDK monkeypatching: drive the profile switch explicitly. Turn off bluetooth.autoswitch-to-headset-profile at runtime, then in talk-key: press, wait for the frame ACK, `wpctl set-profile` HFP, record, release, `wpctl set-profile` A2DP. A host-rendered Listening image sent before the press (the HANDOFF's 'вариант 1') also works without touching the SDK.
- Cheap protocol-side experiments not considered: bugzmanov reports a second JL_SPP record on RFCOMM channel 10 (FINDINGS.md:20). Whether channel 10, or a tiny command (brightness 0x32 / screen 0xBD), gets any response during steady HFP would distinguish 'whole app task stalled' from '0x8B decoder stalled'. The BLE AF30/fe010000 channel listed in HANDOFF 5.2 is also not assessed; sources say BLE is not used for commands (bugzmanov FINDINGS.md:16, 35), so its prior is low.
- PipeWire's dummy '+CIEV: 2,1' is sent at SCO connect start, before the SCO link is up (backend-native.c:2808-2810 precede sco_ready). The firmware's call-state change can therefore precede the eSCO setup, which matters for any 'transitions-only' gate timing.
- In the USB-topology option, the MiniToo's HID Consumer Control keys would also give Linux a Play/Pause source independent of AVRCP. That directly addresses 5.1/5.3 (buttons during recording), which the report only mentions in passing. FINDINGS.md:85 (SPP_CHANGE_MODE incl. UAC) suggests a UAC source mode, so check whether A2DP or SPP behave differently in that mode.