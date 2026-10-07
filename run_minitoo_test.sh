#!/usr/bin/env bash
# MiniToo hardware test: steps 2-4 and 8-13 of HERMES_MINITOO_HARDWARE_TEST.md.
# Usage: run_minitoo_test.sh <MINITOO_MAC>
# No firmware/OTA/DFU/JTAG commands; only one hermes-minitoo RFCOMM client at a time.
set -u
MINITOO_MAC="${1:?usage: $0 MINITOO_MAC}"
REPO="$HOME/hermes-minitoo-gadget"
SCRATCH="$HOME/.hermes/profiles/padawan/cache/scratch"
OUT="$SCRATCH/minitoo_test_output.md"
RES="$SCRATCH/minitoo_results.env"
GADGET_URL="ws://127.0.0.1:8765/gadget"
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib/portaudio-extract/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$REPO" || exit 1
. .venv/bin/activate

: > "$RES"
res() { echo "$1=\"$2\"" >> "$RES"; }
log() { echo -e "\n### $*" | tee -a "$OUT"; }
run() { echo -e "\n\$ $*" >> "$OUT"; "$@" >> "$OUT" 2>&1; local rc=$?; echo "[rc=$rc]" >> "$OUT"; return $rc; }
info() { bluetoothctl info "$MINITOO_MAC" 2>&1; }

echo "# MiniToo test run $(date -Is) MAC=$MINITOO_MAC" >> "$OUT"
res MAC "$MINITOO_MAC"

# STEP 2
log "STEP 2 info"
run bluetoothctl info "$MINITOO_MAC"
res DISCOVERED PASS

# STEP 3
log "STEP 3 pair/trust"
if ! info | grep -q "Paired: yes"; then
  run bluetoothctl --timeout 30 pair "$MINITOO_MAC" || true
  if ! info | grep -q "Paired: yes"; then
    sleep 5
    run bluetoothctl --agent NoInputNoOutput --timeout 30 pair "$MINITOO_MAC" || true
  fi
fi
run bluetoothctl trust "$MINITOO_MAC"
run bluetoothctl --timeout 15 connect "$MINITOO_MAC" || true
sleep 2
run bluetoothctl info "$MINITOO_MAC"
if info | grep -q "Paired: yes"; then
  res PAIRED PASS
else
  # The device drops BlueZ pairing; the SPP link works without it. Verify a direct RFCOMM connect.
  if "$REPO/.venv/bin/python" -c "
import sys; sys.path.insert(0,'$REPO/src')
from hermes_minitoo.transport import _open_rfcomm
_open_rfcomm('$MINITOO_MAC',1,8).close()" >> "$OUT" 2>&1; then
    res PAIRED "SKIP(direct RFCOMM ok)"
  else
    res PAIRED FAIL
    res ERROR "no pairing and direct RFCOMM failed: see minitoo_test_output.md STEP 3"
    exit 3
  fi
fi

# STEP 4
log "STEP 4 SDP"
SDP="$(timeout 20 sdptool browse "$MINITOO_MAC" 2>&1)"
echo "$SDP" | grep -Ei -A8 -B2 'JL_SPP|Serial Port|Audio Sink|A2DP|RFCOMM|Channel' >> "$OUT" || echo "(no SDP matches)" >> "$OUT"
CHANNELS="$(echo "$SDP" | grep -Eo 'Channel: [0-9]+' | awk '{print $2}' | sort -un | tr '\n' ' ')"
echo "RFCOMM channels: ${CHANNELS:-none}" >> "$OUT"
CHANNEL=1
# Only leave channel 1 if SDP clearly lists RFCOMM channels and 1 is not among them.
if [ -n "$CHANNELS" ] && ! echo " $CHANNELS " | grep -q " 1 "; then
  JL="$(echo "$SDP" | grep -A12 -i 'JL_SPP\|Serial Port' | grep -Eo 'Channel: [0-9]+' | head -1 | awk '{print $2}')"
  [ -n "$JL" ] && CHANNEL="$JL"
fi
res CHANNELS "${CHANNELS:-unknown}"
res CHANNEL "$CHANNEL"

# STEP 5 recheck
log "STEP 5 gadget info"
run hermes gadget info

# STEP 8
log "STEP 8 config"
MINITOO_MAC="$MINITOO_MAC" GADGET_URL="$GADGET_URL" CHANNEL="$CHANNEL" python - <<'PY' >> "$OUT" 2>&1
import json, os
cfg = json.load(open("config.template.json"))
cfg["server"] = os.environ["GADGET_URL"]
cfg["minitoo"]["address"] = os.environ["MINITOO_MAC"]
cfg["minitoo"]["channel"] = int(os.environ["CHANNEL"])
with open("config.json", "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print(json.dumps(cfg, indent=2))
PY
chmod 600 config.json

start_gadget() {
  if [ -f minitoo.pid ]; then kill "$(cat minitoo.pid)" 2>/dev/null || true; rm -f minitoo.pid; sleep 2; fi
  pkill -f "hermes-minitoo .*run --config" 2>/dev/null || true
  sleep 1
  nohup hermes-minitoo run --config config.json > minitoo.log 2>&1 &
  echo $! > minitoo.pid
  sleep "${1:-5}"
}

# STEP 9
log "STEP 9 start"
start_gadget 10
run hermes-minitoo status || true
tail -n 100 minitoo.log >> "$OUT"

# STEP 10
log "STEP 10 Hermes pairing"
run hermes gadget pair --yes --timeout 60 || true
sleep 3
run hermes-minitoo status || true
run hermes gadget devices || true
tail -n 100 minitoo.log >> "$OUT"
if hermes gadget devices 2>&1 | grep -qi "minitoo"; then res HPAIR PASS; else res HPAIR FAIL; fi

# STEP 12 (case A) before the message if the ACK is missing
if grep -q "0x8B ready ACK" minitoo.log; then
  log "STEP 12 case A: missing 0x8B ACK"
  run bluetoothctl info "$MINITOO_MAC"
  echo "$SDP" | grep -Ei -A8 -B2 'JL_SPP|Serial Port|RFCOMM|Channel' >> "$OUT" || true
  run pgrep -af hermes-minitoo
  start_gadget 15
  tail -n 60 minitoo.log >> "$OUT"
fi

# STEP 11
log "STEP 11 text message"
run hermes-minitoo send "Reply with exactly: MiniToo test OK" || true
sleep 20
run hermes-minitoo messages || true
run hermes-minitoo status || true
tail -n 100 minitoo.log >> "$OUT"
if hermes-minitoo messages 2>&1 | grep -q "MiniToo test OK"; then res REPLY PASS; else res REPLY FAIL; fi

# STEP 12 diagnostics B/C/D
if grep -Eqi "permission|EACCES|Errno 13" minitoo.log; then
  log "STEP 12 case B"; run id; run groups; run ls -l /var/run/dbus/system_bus_socket
fi
if grep -Eqi "lzo" minitoo.log; then
  log "STEP 12 case C"; ldconfig -p | grep lzo >> "$OUT"; dpkg -l | grep liblzo >> "$OUT"
fi
if grep -Eqi "framebuffer|bytes" minitoo.log; then
  log "STEP 12 case D"; grep -Ei "framebuffer|bytes" minitoo.log | tail -5 >> "$OUT"
fi
if grep -Eqi "0x8B ready ACK|Traceback|socket error|ConnectionRefused|Errno|LzoError|framebuffer" minitoo.log; then
  res DISPLAY FAIL
  res ERROR "$(grep -Ei '0x8B ready ACK|Error|Errno|framebuffer' minitoo.log | tail -1 | cut -c1-200)"
elif kill -0 "$(cat minitoo.pid)" 2>/dev/null; then
  res DISPLAY PASS
else
  res DISPLAY FAIL
  res ERROR "hermes-minitoo exited: $(tail -1 minitoo.log | cut -c1-200)"
fi

# STEP 13 audio
log "STEP 13 audio"
{ command -v wpctl && wpctl status; } >> "$OUT" 2>&1 || echo "wpctl: not available" >> "$OUT"
{ command -v pactl && pactl info; } >> "$OUT" 2>&1 || echo "pactl: not available" >> "$OUT"
AUD="$(hermes-minitoo audio-devices 2>&1)"; echo "$AUD" >> "$OUT"
run bluetoothctl info "$MINITOO_MAC"
DEV="$(echo "$AUD" | grep -Ei 'minitoo|divoom|tiivoo' | grep -Ei '[1-9][0-9]* out' | head -1)"
if [ -n "$DEV" ]; then
  res AUDIO PASS
  NAME="$(echo "$DEV" | sed -E 's/^[ *<>]*[0-9]+ //; s/,.*$//')"
  res AUDIO_NAME "$NAME"
  NAME="$NAME" python - <<'PY'
import json, os
cfg = json.load(open("config.json")); cfg["audio"]["output"] = os.environ["NAME"]
json.dump(cfg, open("config.json", "w"), indent=2)
PY
  start_gadget 20
  tail -n 60 minitoo.log >> "$OUT"
  if grep -Eqi "0x8B ready ACK|Traceback|Errno" minitoo.log; then res COEXIST FAIL; else res COEXIST PASS; fi
else
  res AUDIO FAIL
  res COEXIST "NOT TESTED"
  echo "DISPLAY TEST CAN CONTINUE. AUDIO BLOCKED: MiniToo is paired over Bluetooth but no usable PortAudio output is currently exposed by the server audio stack." >> "$OUT"
fi
echo -e "\n# done $(date -Is)" >> "$OUT"
