#!/usr/bin/env bash
# steady-hfp.sh — отвечает ли колонка на кадры экрана, пока HFP уже установлен
# (HANDOFF §5.2; D1 из исследования display-link).
#
# Вопрос: экран «молчит» весь период HFP или только на переключениях A2DP <-> HFP?
# Что делает (после подтверждения): держит микрофон колонки (audio.input из config.json)
# открытым N секунд (по умолчанию 60) через pw-record в /dev/null. WirePlumber, как при
# обычной записи, переводит колонку в HFP (PipeWire открывает SCO и сообщает колонке
# фиктивный «звонок»), и держит так, пока идёт запись. Экран Ready тем временем
# анимируется, hermes-minitoo шлёт кадры. Скрипт показывает журнал hermes-minitoo и
# таблицу соединений (links.py: когда поднялся/упал eSCO), пишет всё в ~/minitoo-diag/.
# Новых команд колонке скрипт не шлёт: это то же переключение профиля, что и при записи.
# Root не нужен. Во время теста не нажимать Play/Pause.
#
# ОГОВОРКА: на коде экрана из origin/main удачная отправка кадра в журнал НЕ пишется
# (видны только retry/recovered), поэтому «нет строк» не отличить от «воркер висит в
# ожидании». Решающим тест становится с новым кодом экрана, запущенным с --verbose:
# он пишет «ready ACK in N ms» на каждый кадр (DEBUG; при > 1 с — INFO).
#
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/steady-hfp.sh [-d СЕКУНД] [-a СЕКУНД_ПОСЛЕ]
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

DURATION=60
AFTER=20
while [ $# -gt 0 ]; do
    case "$1" in
        -d | --duration) DURATION="${2:?нужно значение}"; shift 2 ;;
        -a | --after) AFTER="${2:?нужно значение}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done
[[ "$DURATION$AFTER" =~ ^[0-9]+$ ]] || { echo "-d/-a: нужно целое число секунд" >&2; exit 2; }
for tool in pw-record journalctl systemctl; do
    have "$tool" || { echo "нет команды $tool" >&2; exit 1; }
done
need_mac
IN="$(conf_get audio input 2>/dev/null)" || IN=""
[ -n "$IN" ] || { echo "в $CONF нет audio.input" >&2; exit 1; }

section "Проверки"
note "колонка: $MAC, микрофон: $IN"
note "hermes-minitoo: $(systemctl --user is-active hermes-minitoo 2>&1)"
if systemctl --user show -P ExecStart hermes-minitoo 2>/dev/null | grep -q -- '--verbose'; then
    VERBOSE=1
    note "hermes-minitoo запущен с --verbose: строки «ready ACK in N ms» будут, если код экрана новый"
else
    VERBOSE=0
    note "hermes-minitoo запущен БЕЗ --verbose: удачные кадры в журнал не попадут (см. оговорку в шапке)."
    note "Для решающего теста: systemctl --user edit hermes-minitoo, в drop-in [Service] ExecStart= (пустая"
    note "строка) и ExecStart=<прежняя команда> --verbose; затем systemctl --user restart hermes-minitoo."
fi
note "talk-key: $(systemctl --user is-active minitoo-talk-key 2>&1) — во время теста не нажимать Play/Pause"

cat <<EOF

Тест откроет микрофон колонки на $DURATION с: колонка перейдёт в HFP, как при обычной записи
(ответов Hermes это не вызывает — звук идёт в /dev/null). Затем ещё $AFTER с наблюдения за
возвратом в A2DP и восстановлением экрана.
EOF
confirm "Начать?" || { echo "отменено"; exit 0; }

LOG="$(log_file steady-hfp)"
exec > >(trap '' INT; exec tee -a "$LOG") 2>&1
PIDS=()
cleanup() {
    trap - EXIT INT TERM
    if [ "${#PIDS[@]}" -gt 0 ]; then
        kill "${PIDS[@]}" 2>/dev/null
        wait "${PIDS[@]}" 2>/dev/null
    fi
    echo
    note "конец $(date +%T.%3N); журнал: $LOG"
}
# Пауза, которую прерывает Ctrl-C (wait прерывается сигналом, а sleep на переднем плане — нет).
pause() {
    sleep "$1" &
    PIDS+=("$!")
    wait "$!"
}
trap cleanup EXIT
trap 'echo; note "прервано"; exit 130' INT TERM

section "Ход теста ($(date '+%F %T'))"
journalctl --user -u hermes-minitoo -f -n 0 -o short-precise &
PIDS+=("$!")
if have python3; then
    python3 -I "$DIAG_DIR/links.py" --no-header --prefix "[links] " &
    PIDS+=("$!")
fi
pause 2
note "T0 $(date +%T.%3N): pw-record на $IN (${DURATION} с)"
pw-record --target="$IN" --rate 16000 --channels 1 --format s16 - >/dev/null &
REC=$!
PIDS+=("$REC")
pause "$DURATION"
kill "$REC" 2>/dev/null
note "T1 $(date +%T.%3N): запись остановлена; ещё $AFTER с наблюдения"
pause "$AFTER"

section "Как читать (между T0 и T1)"
cat <<EOF
- «ready ACK in N ms» в середине окна (через несколько секунд после появления eSCO в [links]) —
  колонка отвечает и в установившемся HFP; экран молчит только на переключениях.
- только «retry … did not send the 0x8B ready ACK» до самого T1 — колонка молчит весь HFP.
- строк нет совсем: на старом коде экрана тест не решающий (см. оговорку в шапке);
  VERBOSE=$VERBOSE.
- «recovered after …» после T1 — экран вернулся после выхода из HFP; время от T1 — цена переключения.
Прислать: файл $LOG целиком.
EOF
