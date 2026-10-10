# shellcheck shell=bash
# Общие функции для scripts/diag/*.sh. Подключается через «. _lib.sh», сам не запускается.
# Ничего не меняет: только читает config.json и печатает.

DIAG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# config.json того репозитория, где лежит скрипт (каталог может называться как угодно);
# если его там нет — прежнее место ~/hermes-minitoo-gadget/config.json.
_REPO_DIR="$(cd "$DIAG_DIR/../.." && pwd)"
if [ -z "${MINITOO_CONFIG:-}" ] && [ -f "$_REPO_DIR/config.json" ]; then
    CONF="$_REPO_DIR/config.json"
else
    CONF="${MINITOO_CONFIG:-$HOME/hermes-minitoo-gadget/config.json}"
fi
DIAG_LOG_DIR="${MINITOO_DIAG_DIR:-$HOME/minitoo-diag}"

section() { printf '\n===== %s =====\n' "$*"; }
note() { printf '# %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
# Справка (--help): шапка-комментарий вызвавшего скрипта.
usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

# Напечатать команду и выполнить её; ошибка команды скрипт не прерывает.
cmd() {
    printf '\n$ %s\n' "$*"
    eval "$*" 2>&1 || true
}

# Значение из config.json по цепочке ключей. Печатается только это значение (не token).
conf_get() {
    python3 -I - "$CONF" "$@" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    value = json.load(f)
for key in sys.argv[2:]:
    value = value[key]
print(value)
PY
}

# MAC колонки из config.json (адрес меняется, поэтому в скриптах его нет).
# Задаёт MAC (B1:21:81:A0:78:53), MAC_US (B1_21_81_A0_78_53) и PFX (первые три байта).
need_mac() {
    MAC="$(conf_get minitoo address 2>/dev/null)" || MAC=""
    if ! [[ "$MAC" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
        echo "Не удалось прочитать minitoo.address из $CONF (другой путь: MINITOO_CONFIG=...)" >&2
        exit 1
    fi
    MAC="${MAC^^}"
    MAC_US="${MAC//:/_}"
    PFX="${MAC:0:8}"
}

# Состоит ли пользователь в группе, которой разрешено читать системный журнал
# (ядро, bluetooth.service). Без неё journalctl -k / -u bluetooth покажет мало или ничего.
journal_ok() {
    [ "$(id -u)" -eq 0 ] && return 0
    id -nG | tr ' ' '\n' | grep -qxE 'adm|systemd-journal|wheel'
}

journal_note() {
    if journal_ok; then
        note "системный журнал: доступ есть"
    else
        note "[нужна группа adm или systemd-journal] пользователь $(id -un) не в них — строки ниже могут быть пустыми;"
        note "добавить (root, сам владелец): sudo usermod -aG systemd-journal $(id -un), затем перелогиниться"
    fi
}

# Подтверждение y/N для шагов, которые трогают радио или колонку.
confirm() {
    local answer=""
    printf '%s [y/N] ' "$1"
    read -r answer || return 1
    case "$answer" in
        y | Y | yes | д | да) return 0 ;;
        *) return 1 ;;
    esac
}

# Каталог и имя файла лога для скриптов, которые работают до Ctrl-C.
log_file() {
    mkdir -p "$DIAG_LOG_DIR"
    printf '%s/%s-%s.log' "$DIAG_LOG_DIR" "$1" "$(date +%Y%m%d-%H%M%S)"
}
