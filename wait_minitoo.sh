#!/usr/bin/env bash
# Scan for MiniToo every 5 minutes until DEADLINE (epoch); run the hardware test when it appears.
set -u
DEADLINE="${1:?usage: $0 DEADLINE_EPOCH}"
REPO="$HOME/hermes-minitoo-gadget"
SCRATCH="$HOME/.hermes/profiles/padawan/cache/scratch"
LOG="$SCRATCH/minitoo_log.md"
RES="$SCRATCH/minitoo_results.env"
REPORT="$SCRATCH/minitoo_report.md"
STATE="$SCRATCH/minitoo_wait_state"
echo "waiting" > "$STATE"

MAC=""
n=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  n=$((n + 1))
  bluetoothctl --timeout 30 scan on > /dev/null 2>&1 || true
  MAC="$(bluetoothctl devices 2>/dev/null | grep -Ei 'minitoo|divoom|tiivoo' | head -1 | awk '{print $2}')"
  echo "- $(date +%H:%M) scan #$n: ${MAC:-not found} ($(bluetoothctl devices 2>/dev/null | wc -l) devices known)" >> "$LOG"
  [ -n "$MAC" ] && break
  left=$((DEADLINE - $(date +%s)))
  [ "$left" -le 0 ] && break
  sleep $(( left < 270 ? left : 270 ))
done

if [ -z "$MAC" ]; then
  echo "- $(date +%H:%M) deadline reached, MiniToo not found -> WAITING FOR PHYSICAL SETUP" >> "$LOG"
  echo "no_device" > "$STATE"
  exit 0
fi

echo "- $(date +%H:%M) found $(bluetoothctl devices | grep "$MAC"); running run_minitoo_test.sh" >> "$LOG"
echo "testing" > "$STATE"
"$REPO/run_minitoo_test.sh" "$MAC"
echo "- $(date +%H:%M) run_minitoo_test.sh exit=$?" >> "$LOG"
echo "tested" > "$STATE"
