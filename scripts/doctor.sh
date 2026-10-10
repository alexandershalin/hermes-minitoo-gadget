#!/usr/bin/env bash
# Самодиагностика установки: что есть, чего не хватает. Ничего не меняет. Код выхода = число проблем.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BAD=0
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; BAD=$((BAD+1)); }
warn() { printf '  [warn] %s\n' "$*"; }

echo "Бинарники"
for c in python3 bluetoothctl wpctl pw-record pw-play ffmpeg; do
  command -v "$c" >/dev/null && ok "$c" || { [ "$c" = ffmpeg ] && warn "$c (нужен STT-клиенту)" || bad "$c не найден"; }
done
ldconfig -p | grep -q liblzo2 && ok liblzo2 || bad "liblzo2 (apt install liblzo2-2)"
ldconfig -p | grep -q libportaudio && ok libportaudio || warn "libportaudio2 не установлен в системе (см. docs/PORTABILITY.md)"

echo "Проект"
[ -x "$REPO/.venv/bin/hermes-gadget-minitoo" ] && ok ".venv + hermes-gadget-minitoo" || bad "нет .venv/bin/hermes-gadget-minitoo (запустите install.sh)"
if [ -f "$REPO/config.json" ]; then
  ok config.json
  A="$(python3 -c "import json;print(json.load(open('$REPO/config.json'))['minitoo']['address'])" 2>/dev/null)"
  case "$A" in ""|REAL_MAC_HERE) bad "minitoo.address не задан";; *) ok "address=$A";; esac
else bad "нет config.json"; fi

echo "Hermes и звук"
SRV="$(python3 -c "import json;print(json.load(open('$REPO/config.json'))['server'])" 2>/dev/null)"
PORT="$(printf '%s' "$SRV" | sed -E 's#.*:([0-9]+)/.*#\1#')"
if [ -n "$PORT" ] && (ss -ltn 2>/dev/null | grep -q ":$PORT "); then ok "gateway слушает :$PORT"; else warn "порт gateway ($PORT) не слушается — gadget-платформа Hermes включена?"; fi
wpctl status >/dev/null 2>&1 && ok "PipeWire/WirePlumber отвечает" || bad "wpctl status не отвечает (PipeWire запущен? есть ли пользовательская сессия?)"

echo "Bluetooth"
if [ -n "${A:-}" ] && [ "$A" != REAL_MAC_HERE ]; then
  I="$(bluetoothctl info "$A" 2>/dev/null)"
  echo "$I" | grep -q "Paired: yes" && ok "колонка сопряжена" || warn "колонка не сопряжена (bluetoothctl pair $A)"
  echo "$I" | grep -q "Connected: yes" && ok "колонка подключена" || warn "колонка не подключена"
fi
for u in hermes-minitoo minitoo-talk-key minitoo-autoaddr; do
  s="$(systemctl --user is-active $u 2>/dev/null)"; [ "$s" = active ] && ok "сервис $u: active" || warn "сервис $u: ${s:-нет}"
done
cat /sys/module/btusb/parameters/enable_autosuspend 2>/dev/null | grep -q N && ok "btusb autosuspend выключен" || warn "btusb autosuspend не выключен (см. scripts/system/README.md)"

echo "Серверная часть Hermes (голос)"
for f in openrouter-stt-client.py piper-tts-client.py; do
  [ -f "$HOME/.local/bin/$f" ] && ok "$f установлен" || warn "$f не установлен (hermes-side/install.sh)"
done
exit "$BAD"
