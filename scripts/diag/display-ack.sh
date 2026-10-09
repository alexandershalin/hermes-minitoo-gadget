#!/usr/bin/env bash
# display-ack.sh — какие именно сбои у экрана (HANDOFF §5.2; D3 из исследования display-link).
#
# Что делает: читает журнал user-сервиса hermes-minitoo за период (по умолчанию 2 суток)
# и считает строки «MiniToo display retry N: <ошибка>» по типам:
#   TimeoutError «did not send the 0x8B ready ACK» — канал RFCOMM открыт, но приложение
#     колонки 8 с молчит (наш таймаут; сокет потом закрываем мы сами);
#   TimeoutError при connect — колонка не ответила на подключение (пейджинг);
#   ConnectionError «closed RFCOMM connection» — колонка закрыла канал (EOF);
#   ConnectionError «reconnect backoff is active» — наша пауза 2 с после сбоя (самонаведённое);
#   errno N — ошибка сокета (16 EBUSY: старый DLC ещё не закрыт; 104 reset; 112 host down...);
# плюс «recovered after N retries (X s)» (распределение X), «update failed after»
# (кадр отброшен по окну повторов) и, если запущен новый код экрана с --verbose,
# «ready ACK in N ms» (задержка подтверждения).
# Только чтение журнала своего пользователя: root и группы не нужны, колонка не трогается.
#
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/display-ack.sh [--since '-2 days'] [--unit hermes-minitoo]
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

SINCE="-2 days"
UNIT="hermes-minitoo"
while [ $# -gt 0 ]; do
    case "$1" in
        --since) SINCE="${2:?нужно значение}"; shift 2 ;;
        --unit) UNIT="${2:?нужно значение}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
journalctl --user -u "$UNIT" --since "$SINCE" --no-pager -o cat >"$TMP" 2>/dev/null || true

section "Журнал $UNIT с '$SINCE': $(wc -l <"$TMP") строк"
note "подключений к колонке (connected to MiniToo): $(grep -c 'connected to MiniToo' "$TMP")"

section "Повторы кадра по типу ошибки (строки 'display retry')"
awk '
function errno_key(s,    m) {
    if (match(s, /\[Errno [0-9]+\][^"\047)]*/))
        return "errno: " substr(s, RSTART + 7, RLENGTH - 7)
    if (match(s, /[A-Za-z]*Error\([0-9]+, \047[^\047]*\047/)) {
        m = substr(s, RSTART, RLENGTH)
        sub(/\(/, " errno ", m); sub(/, \047/, " ", m); sub(/\047$/, "", m)
        return "errno: " m
    }
    return ""
}
/display retry [0-9]+:/ {
    total++
    if ($0 ~ /did not send the 0x8B ready ACK/) k = "TimeoutError: нет 0x8B ready ACK (колонка молчит, DLC открыт)"
    else if ($0 ~ /RFCOMM connect timed out|TimeoutError\(\047timed out\047\)/) k = "TimeoutError: connect (колонка не ответила на подключение)"
    else if ($0 ~ /closed RFCOMM connection/) k = "ConnectionError: колонка закрыла RFCOMM (EOF)"
    else if ($0 ~ /reconnect backoff is active/) k = "ConnectionError: наш reconnect backoff 2 с"
    else if ((k = errno_key($0)) == "") {
        k = $0; sub(/.*display retry [0-9]+: /, "", k); k = "прочее: " substr(k, 1, 70)
    }
    n[k]++
}
END {
    if (!total) { print "  (строк display retry нет)"; exit }
    for (k in n) printf "%7d  %s\n", n[k], k
    printf "%7d  всего\n", total
}' "$TMP" | sort -rn

section "Восстановления ('recovered after N retries (X s)')"
grep -oE 'recovered after [0-9]+ retries \([0-9.]+s\)' "$TMP" \
    | sed -E 's/.*\(([0-9.]+)s\)/\1/' | sort -n \
    | awk '{ v[NR] = $1 } END {
        if (!NR) { print "  (нет)"; exit }
        printf "  %d раз; длительность, с: мин %s, медиана %s, макс %s\n", NR, v[1], v[int((NR + 1) / 2)], v[NR]
    }'
note "отсчёт идёт от начала НОВОГО кадра: брошенный кадр с его 8-секундным ожиданием сюда не входит"

section "Кадры, отброшенные по окну повторов ('update failed after')"
note "$(grep -c 'display update failed after' "$TMP") раз"

section "Задержка ready ACK ('ready ACK in N ms', только новый код экрана; DEBUG — с --verbose)"
grep -oE 'ready ACK in [0-9]+ ms' "$TMP" | grep -oE '[0-9]+' | sort -n \
    | awk '{ v[NR] = $1 } END {
        if (!NR) { print "  (нет: старый код экрана или сервис без --verbose — успешные отправки не логируются)"; exit }
        printf "  %d раз; мс: мин %s, медиана %s, макс %s\n", NR, v[1], v[int((NR + 1) / 2)], v[NR]
    }'

section "Как читать"
cat <<'EOF'
Преобладает «нет 0x8B ready ACK» — канал не рвётся, молчит приложение колонки (вероятно,
пока она в режиме HFP/«звонка»). Преобладают EOF/errno — настоящие разрывы канала или ACL
(смотреть bt-health.sh, links.py). Много «наш reconnect backoff» — это последствия первой
ошибки, а не отдельная проблема. Новый код экрана выводит не каждый повтор (первые 5, затем
каждый 30-й подряд), поэтому для него счётчики занижены.
Прислать: весь вывод этого скрипта (адресов и токенов в нём нет).
EOF
