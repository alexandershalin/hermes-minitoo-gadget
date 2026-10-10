#!/usr/bin/env bash
# Серверная часть голоса для Hermes: STT-клиент (OpenRouter Whisper) и локальный TTS Piper (ru+en).
#   ./hermes-side/install.sh [--hermes-venv PATH] [--voices-dir PATH] [--no-tts]
# Ничего не пишет в config.yaml сам: нужные строки выводятся в конце (применять через `hermes config set`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HVENV="${HERMES_VENV:-$HOME/.hermes/hermes-agent/venv}" VOICES="$HOME/.local/share/piper-voices" TTS=1
while [ $# -gt 0 ]; do case "$1" in
  --hermes-venv) HVENV="$2"; shift 2;; --voices-dir) VOICES="$2"; shift 2;; --no-tts) TTS=0; shift;;
  *) echo "неизвестный аргумент $1" >&2; exit 2;; esac; done
B="$HOME/.local/bin"; mkdir -p "$B"
for f in openrouter-stt-client.py piper-tts-client.py; do
  [ -f "$B/$f" ] && ! cmp -s "$HERE/$f" "$B/$f" && cp "$B/$f" "$B/$f.bak"
  install -m 755 "$HERE/$f" "$B/$f"; echo "установлен $B/$f"
done
if [ "$TTS" = 1 ]; then
  [ -x "$HVENV/bin/python" ] || { echo "нет $HVENV/bin/python — укажите --hermes-venv (venv с пакетом piper-tts)" >&2; exit 1; }
  "$HVENV/bin/python" -c "import piper" 2>/dev/null || "$HVENV/bin/python" -m pip install -q piper-tts
  mkdir -p "$VOICES"
  for v in ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium en/en_US/ryan/medium/en_US-ryan-medium; do
    n="$(basename $v)"
    for ext in onnx onnx.json; do
      [ -f "$VOICES/$n.$ext" ] || curl -fsSL -o "$VOICES/$n.$ext" "https://huggingface.co/rhasspy/piper-voices/resolve/main/$v.$ext"
    done
  done
  install -m 755 "$HERE/piper-tts-daemon.py" "$B/piper-tts-daemon.py"
  U="$HOME/.config/systemd/user"; mkdir -p "$U"
  sed "s#%h/.hermes/hermes-agent/venv#$HVENV#" "$HERE/piper-tts.service" > "$U/piper-tts.service"
  systemctl --user daemon-reload && systemctl --user enable --now piper-tts
  echo "piper-tts запущен на 127.0.0.1:8770 (грузит модели ~10 с)"
fi
cat <<MSG

Добавьте в config.yaml Hermes (hermes config set ...):
  stt.provider: openrouter_stt            # command-провайдер, см. docs/PORTABILITY.md
  stt.language: auto                      # Whisper сам определяет ru/en
  tts.provider: piper_daemon              # command-провайдер, см. docs/PORTABILITY.md
Ключ OPENROUTER_API_KEY берётся из окружения, \$HERMES_HOME/.env или shared/secrets.env.
Прокси для STT (необязательно): ~/.config/hermes-minitoo/stt.env -> OR_STT_PROXY=http://127.0.0.1:PORT
MSG
