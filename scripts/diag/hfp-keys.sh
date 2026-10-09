#!/usr/bin/env bash
# hfp-keys.sh — куда уходит Play/Pause во время записи (HANDOFF §5.1, §5.3).
#
# Что делает: на время теста поднимает уровень лога WirePlumber ТОЛЬКО для темы
# spa.bluez5.native до I (INFO; с --debug до D — полный обмен AT-командами), показывает
# журнал WirePlumber этой темы (поле TOPIC) и сохраняет его в ~/minitoo-diag/. При выходе
# (Ctrl-C) возвращает уровень: "N,spa.bluez5.native:I", если установлен фрагмент
# scripts/wireplumber/60-minitoo-hfp-at-log.conf, иначе "N" (уровень WirePlumber по
# умолчанию). «wpctl set-log-level -» не используется намеренно: он стирает и шаблон из
# conf.d-фрагмента до перезапуска WirePlumber.
# Root не нужен, WirePlumber не перезапускается. Колонке ничего не отправляется: меняется
# только то, что WirePlumber пишет в свой журнал.
#
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/hfp-keys.sh [--debug] [--grep]
#   --debug  уровень D вместо I (видны и обработанные команды: AT+VGS, +CIEV, AT+CMER)
#   --grep   фильтровать по тексту, а не по полю TOPIC (если с TOPIC ничего не видно)
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

LEVEL=I
USE_TOPIC=1
for arg in "$@"; do
    case "$arg" in
        --debug) LEVEL=D ;;
        --grep) USE_TOPIC=0 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "неизвестный аргумент: $arg" >&2; exit 2 ;;
    esac
done

for tool in wpctl journalctl; do
    have "$tool" || { echo "нет команды $tool" >&2; exit 1; }
done

FRAG="$HOME/.config/wireplumber/wireplumber.conf.d/60-minitoo-hfp-at-log.conf"
if [ -f "$FRAG" ]; then
    RESTORE="N,spa.bluez5.native:I"
    WHY="установлен $FRAG — возвращаю его уровень"
else
    RESTORE="N"
    WHY="фрагмента 60-minitoo-hfp-at-log.conf нет — возвращаю уровень WirePlumber по умолчанию (N)"
fi
LOG="$(log_file hfp-keys)"

section "Текущее состояние"
cmd "systemctl --user show wireplumber -p Environment"
if systemctl --user show-environment 2>/dev/null | grep -q '^WIREPLUMBER_DEBUG='; then
    note "ВНИМАНИЕ: в окружении user-менеджера задан WIREPLUMBER_DEBUG — фрагменты conf.d с log.level не действуют"
fi
cmd "pw-metadata -n settings 2>/dev/null | grep -F log.level || echo '(log.level во время работы не менялся)'"

restore() {
    trap - EXIT INT TERM
    if wpctl set-log-level "$RESTORE"; then
        echo
        note "уровень лога WirePlumber возвращён: \"$RESTORE\" ($WHY)."
    else
        echo
        note "НЕ удалось вернуть уровень. Выполните вручную: wpctl set-log-level \"$RESTORE\""
    fi
    note "(не «wpctl set-log-level -»: он сбросил бы и шаблон из conf.d до перезапуска WirePlumber)"
    note "журнал теста: $LOG"
}

wpctl set-log-level "N,spa.bluez5.native:$LEVEL" || { echo "wpctl set-log-level не сработал" >&2; exit 1; }
trap restore EXIT
trap 'exit 130' INT TERM

section "Что делать (уровень spa.bluez5.native:$LEVEL включён)"
cat <<EOF
Во втором терминале:  journalctl --user -u minitoo-talk-key -f
В третьем (по желанию): python3 -I $DIAG_DIR/links.py
1. Нажмите Play/Pause — начнётся запись (как обычно).
2. Во время записи: один раз Play/Pause; подождать 3 с; два раза быстро; удержать 3 с;
   джойстик влево / вправо / вверх / вниз; громкость + и -.
3. Сразу после конца записи (в первые ~6 с) — один раз Play/Pause.
4. Когда профиль вернётся в A2DP (музыка/ответ снова звучат) — ещё раз Play/Pause.
5. Посмотрите на экран колонки во время записи: нет ли значка звонка/трубки?
6. Ctrl-C здесь — уровень лога вернётся сам.
Что искать ниже:
  "modem not available: AT+CHUP" (или AT+BVRA, ATA, AT+CKPD, AT+BLDN, ATD...) в момент нажатия —
    кнопка приходит AT-командой по HFP, а не по AVRCP;
  с --debug ещё "RFCOMM <<"/"RFCOMM >>", "+CIEV: 2,1" (фиктивный звонок), "AT+VGS" (громкость);
  ни одной строки при нажатиях — колонка ничего не шлёт по HFP; дальше только btmon (root, сам владелец).
Журнал сохраняется в $LOG
EOF

section "Журнал WirePlumber (spa.bluez5.native), с этого момента"
if [ "$USE_TOPIC" -eq 1 ]; then
    journalctl --user -u wireplumber -f -n 0 -o short-precise TOPIC=spa.bluez5.native \
        | (trap '' INT; exec tee -a "$LOG")
else
    journalctl --user -u wireplumber -f -n 0 -o short-precise \
        | (trap '' INT; exec grep --line-buffered -E 'spa\.bluez5\.native|RFCOMM|modem not available|AT\+' | tee -a "$LOG")
fi
